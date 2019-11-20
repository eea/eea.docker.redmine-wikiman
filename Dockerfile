FROM python:3-alpine
LABEL maintainer="EEA: IDM2 A-Team <eea-edw-a-team-alerts@googlegroups.com>"

RUN apk add --no-cache --virtual .run-deps tzdata subversion && \
    pip install python-redmine svn

COPY src/* /

COPY docker-entrypoint.sh /

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["run"]
