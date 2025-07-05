FROM registry.access.redhat.com/ubi9/python-39:1-197.1725907694
ARG release=main
ARG version=latest

LABEL com.redhat.component jira-unfurl-bot
LABEL description "Slack bot that unfurls Jira issues shared in Slack channels"
LABEL summary "Slack bot that unfurls Jira issues shared in Slack channels"
LABEL io.k8s.description "Slack bot that unfurls Jira issues shared in Slack channels"
LABEL distribution-scope public
LABEL name jira-unfurl-bot
LABEL release ${release}
LABEL version ${version}
LABEL url https://github.com/openshift-assisted/jira-unfurl-bot
LABEL vendor "Red Hat, Inc."
LABEL maintainer "Red Hat"

# License
USER 0
RUN mkdir /licenses/ && chown 1001:0 /licenses/

COPY certs/2015-IT-Root-CA.pem /etc/pki/ca-trust/source/anchors/RH-IT-Root-CA.crt
COPY certs/2022-IT-Root-CA.pem /etc/pki/ca-trust/source/anchors/2022-IT-Root-CA.pem
RUN update-ca-trust extract

RUN dnf install -y krb5-workstation
RUN curl --retry 5 --retry-all-errors -k https://gitlab.cee.redhat.com/it-iam/system-configs/raw/master/krb5/idm/linux-krb5.conf -o /etc/krb5.conf
ENV KRB5CCNAME="FILE:/tmp/krb5ccname"

ENV REQUESTS_CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt
ENV SSL_CERT_FILE=/etc/pki/tls/certs/ca-bundle.crt

USER 1001
COPY LICENSE /licenses/

COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt pip-licenses && pip-licenses -l -f json --output-file /licenses/licenses.json

COPY . .
CMD [ "python3", "jira-unfurl-bot.py" ]
