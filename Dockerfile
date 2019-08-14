FROM python:3-alpine
LABEL maintainer="EEA: IDM2 A-Team <eea-edw-a-team-alerts@googlegroups.com>"

RUN apk add --no-cache --virtual .run-deps tzdata && \
    pip install python-redmine

COPY src/* /

COPY docker-entrypoint.sh /

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["run"]
