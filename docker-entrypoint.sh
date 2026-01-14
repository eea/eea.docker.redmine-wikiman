#!/bin/sh

set -e

if [ -n "$ENV_PATH" ] && [ -f "$ENV_PATH" ]; then
  source  "$ENV_PATH"
fi

#run no more than 12h by default
TIMEOUT=${TIMEOUT:-43200}

if [[ "$@" == "run" ]]; then
  echo "Checking mandatory environment variables"
  flag=""

  if [[ "$DEBUG" == "Yes" ]]; then
    flag="${flag} -v"
  fi

  python /wait_for_redmine.py

  if [ -n "$WIKI_SERVER" ] && [ -n "$WIKI_APIKEY" ] && [ -n "$WIKI_PROJECT" ]; then
    if [ -n "$WIKI_STACKS_PAGE" ] && [ -n "$WIKI_HOSTS_PAGE" ] && [ -n "$WIKI_CONTAINERS_PAGE" ]; then
      echo "Received Rancher1 WIKI related variables"

      if [ -n "$RANCHER_CONFIG" ] && [ -n "$WIKI_PAGE" ]; then
        echo "List Rancher1 data"
        timeout $TIMEOUT python /run_all.py $flag -p "$WIKI_PAGE" 2>&1
      fi
    fi

    if [ -n "$WIKI_APPS_PAGE" ] && [ -n "$WIKI_NODES_PAGE" ] && [ -n "$WIKI_PODS_PAGE" ]; then
      echo "Received Rancher2 WIKI related variables"

      if [ -n "$RANCHER2_SERVER_URL" ] && [ -n "$RANCHER2_CLUSTER_ID" ] && [ -n "$RANCHER2_CLUSTER_NAME" ]; then
        echo "List Rancher2 cluster data"
        timeout $TIMEOUT python /run_all_rancher2.py list 2>&1
      fi

      if [ -n "$RANCHER2_CLUSTERS_TO_MERGE" ]; then
        echo "Merge Rancher2 clusters"
        timeout $TIMEOUT python /run_all_rancher2.py merge 2>&1
      fi
    fi
  else
    echo "Did not receive all Taskman related variables"
  fi
else
  exec  "$@"
fi
