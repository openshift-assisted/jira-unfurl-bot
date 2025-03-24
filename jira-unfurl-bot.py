import logging
import os
import re
import sys
from urllib.parse import urlparse

import jira
import requests
from fastapi import FastAPI, Request
from jira.resources import Version
from slack_bolt import App
from slack_bolt.adapter.fastapi import SlackRequestHandler

slack_bot_token: str = os.environ["SLACK_BOT_TOKEN"]
slack_signing_secret: str = os.environ["SLACK_SIGNING_SECRET"]
jira_access_token: str = os.environ["JIRA_ACCESS_TOKEN"]
intellitldr_token: str = os.environ["INTELLITLDR_TOKEN"]

app = App(token=slack_bot_token, signing_secret=slack_signing_secret)
handler = SlackRequestHandler(app)

# FastAPI app
api = FastAPI()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Create a logger for this module
logger = logging.getLogger(__name__)

JIRA_SERVER = "https://issues.redhat.com"
INTELLITLDR_API = "https://intellitldr.corp.redhat.com/api/summarizer/v1/summarize-issue"
ISSUE_TYPE_TO_COLOR = {
    "Epic": "#4c00b0",
    "Task": "#1c4966",
    "Bug": "#7c0a02",
    "Story": "#3bb143",
}
ISSUE_TYPE_TO_ICON = {
    "Epic": "jiraepic",
    "Bug": "jirabug",
    "Task": "jiratask",
    "Story": "jirastory",
}
ISSUE_TYPE_TO_PRIORITY = {
    "Epic": 1,
    "Bug": 2,
    "Story": 3,
    "Task": 4,
}
MAX_SHOWN_ISSUES_IN_VERSION = 10

jira_client = jira.JIRA(JIRA_SERVER, token_auth=jira_access_token)


def get_intellitldr_summary(issue_key: str):
    if not intellitldr_token:
        logger.warning("INTELLITLDR_TOKEN environment variable is not set")
        return None
    url = f"{INTELLITLDR_API}?key={issue_key}"
    headers = {"Authorization": f"Bearer {intellitldr_token}"}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.exception(f"Error fetching IntelliTldr summary for issue {issue_key}: {e}")
        return None


# Check liveness
@app.event("app_mention")
def event_test(say) -> None:  # noqa: ANN001
    say("I'm alive")


@app.event("link_shared")
def got_link(client, payload) -> None:  # noqa: ANN001
    for link in payload["links"]:
        url = link["url"]
        logger.info(f"Link shared: {url}")
        _payload = None
        try:
            parsed_url = urlparse(url)
            path_parts = parsed_url.path.split("/")

            if "browse" in path_parts:
                issue_id = path_parts[-1]
                issue = jira_client.issue(issue_id)
                _payload = get_issue_payload(issue, url)
            elif "versions" in path_parts:
                version_id = path_parts[-1]
                version = jira_client.version(version_id)
                _payload = get_version_payload(version, url)
            elif "projects" == path_parts[1] and "issues" == path_parts[3]:
                issue_id = path_parts[4]
                issue = jira_client.issue(issue_id)
                _payload = get_issue_payload(issue, url)
            else:
                logger.warning(f"Unrecognized Jira URL structure: {url}")

            if _payload is not None:
                client.chat_unfurl(
                    channel=payload["channel"],
                    ts=payload["message_ts"],
                    unfurls=_payload,
                )
            else:
                logger.info(f"No payload generated for URL: {url}")
        except Exception as e:
            logger.exception(f"Error processing URL {url}: {e}")


def get_version_payload(version: Version, url: str):
    release_info = f"Released at {version.releaseDate}" if version.released else "Unreleased"

    text = f":jira: *{version.name}* [*{release_info}*]"

    description = version.raw.get("description")
    if description is not None:
        text += f" : {description}"

    jql_filter = f'project = {version.projectId} AND fixVersion = "{version.name}"'
    if jira_client.version_count_related_issues(version.id)["issuesFixedCount"] > MAX_SHOWN_ISSUES_IN_VERSION:
        # if too much issues are linked to the version, show only bugs and epics
        jql_filter += " AND issuetype in (Bug, Epic, Story)"

    linked_issues = jira_client.search_issues(jql_str=jql_filter)
    linked_issues.sort(
        key=lambda issue: ISSUE_TYPE_TO_PRIORITY[issue["fields"]["issuetype"]["name"]],
    )

    for issue in linked_issues[:MAX_SHOWN_ISSUES_IN_VERSION]:
        icon = ISSUE_TYPE_TO_ICON.get(issue["fields"]["issuetype"]["name"], "jira-1992")
        text += f"\n\t\t:{icon}: <{issue['permalink']()}|{issue['fields']['summary']}>"

    if len(linked_issues) > MAX_SHOWN_ISSUES_IN_VERSION:
        text += (
            f"\n\t\t... ({len(linked_issues) - MAX_SHOWN_ISSUES_IN_VERSION} more epics/bugs to show. <{url}|See more>)"
        )

    return {
        url: {
            "color": "#ff8b3d",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": text,
                    },
                },
            ],
        },
    }


def get_issue_payload(issue, url):
    color = ISSUE_TYPE_TO_COLOR.get(issue.fields.issuetype.name, "#025BA6")

    # Create the basic issue information block
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":jira: *{issue.key}* [*{issue.fields.status.name}*] : {issue.fields.summary}",
            },
        }
    ]

    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View AI Summary", "emoji": True},
                    "action_id": f"view_summary_{issue.key}",
                    "value": url,
                }
            ],
        }
    )

    return {
        url: {
            "color": color,
            "blocks": blocks,
        },
    }


@app.action(re.compile("view_summary_.*"))
def handle_view_summary(ack, body, client):
    logger.info(f"Received action payload: {body}")
    ack()

    action_id = body["actions"][0]["action_id"]
    issue_key = action_id.replace("view_summary_", "")
    url = body["actions"][0]["value"]

    # Helper: extract channel and timestamp from the payload
    def extract_container_details(payload):
        # Try to extract from "container" first
        if "container" in payload and "message_ts" in payload["container"]:
            ts = payload["container"]["message_ts"]
            # Use channel_id from container if available, otherwise fallback to body channel
            channel_id = payload["container"].get("channel_id", payload["channel"]["id"])
        # Fallback: use the "message" field's ts
        elif "message" in payload and "ts" in payload["message"]:
            ts = payload["message"]["ts"]
            channel_id = payload["channel"]["id"]
        else:
            ts = None
            channel_id = None
        return channel_id, ts

    channel_id, ts = extract_container_details(body)
    if not ts or not channel_id:
        logger.error("Could not extract message timestamp or channel id from payload.")
        return

    # Get the summary for the issue
    summary_data = get_intellitldr_summary(issue_key)

    if summary_data:
        try:
            issue = jira_client.issue(issue_key)
            color = ISSUE_TYPE_TO_COLOR.get(issue.fields.issuetype.name, "#025BA6")
            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":jira: *{issue.key}* [*{issue.fields.status.name}*] : {issue.fields.summary}",
                    },
                },
                {"type": "divider"},
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*AI Summary*:\n{summary_data['summary']}"}},
            ]

            client.chat_unfurl(
                channel=channel_id,
                ts=ts,
                unfurls={
                    url: {
                        "color": color,
                        "blocks": blocks,
                    },
                },
            )
        except Exception as e:
            logger.exception(f"Error updating unfurl with summary: {e}")


@api.post("/slack/events")
async def endpoint(req: Request):
    # Get the raw request body
    body = await req.body()

    # Get the headers
    headers = req.headers

    # Log the incoming request for debugging
    logger.info(f"Received request at /slack/events with content-type: {headers.get('content-type')}")

    # Check if this is a URL verification request
    if req.headers.get("content-type") == "application/json":
        body_json = await req.json()
        if body_json.get("type") == "url_verification":
            logger.info("Handling URL verification challenge")
            return {"challenge": body_json["challenge"]}

    # Log that we're about to handle the request
    logger.info("Handling request with SlackRequestHandler")

    # Process with the handler (handles both events and interactive components)
    return await handler.handle(req)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(api, host="0.0.0.0", port=int(os.environ.get("PORT", 3000)))
