FROM python:3-alpine
LABEL maintainer="EEA: IDM2 A-Team <eea-edw-a-team-alerts@googlegroups.com>"

RUN apk add --no-cache --virtual .run-deps tzdata subversion nano git && \
    pip install kubernetes python-redmine svn more-itertools requests natsort pyyaml zipfile36 gitpython && \
    mkdir -p /logs

COPY src/. /

COPY docker-entrypoint.sh /

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["run"]
