FROM registry.access.redhat.com/ubi9/python-39:1-192.1722518946 
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .
CMD [ "python3", "jira-unfurl-bot.py" ]
