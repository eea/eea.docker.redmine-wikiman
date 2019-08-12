FROM python:2-alpine
LABEL maintainer="EEA: IDM2 A-Team <eea-edw-a-team-alerts@googlegroups.com>"

RUN pip install python-redmine

COPY src/* /

COPY docker-entrypoint.sh /

ENTRYPOINT ["/docker-entrypoint.sh"]

