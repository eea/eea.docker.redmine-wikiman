# Redmine wiki manager

Improves redmine wikis adding Rancher, GitHub and DockerHub links.
The image is created under the name: eeacms/redmine-wikiman


## Variables

1. RANCHER_CONFIG - Rancher configuration, Multiline, with the format - rancher url, access key, secret key ( separated by commas)
2. WIKI_SERVER - Redmine url - The main url of the redmine instance.
3. WIKI_APIKEY - Redmine API key - Belongs to the user that will update the wiki pages.
4. WIKI_PROJECT - Redmine wiki project, the redmine project where the wiki pages are.
5. WIKI_HOSTS_PAGE - Redmine wiki hosts page
6. WIKI_STACKS_PAGE - Redmine wiki stacks page
7. WIKI_CONTAINERS_PAGE - Redmine wiki containers page
8. WIKI_PAGE - Redmine wiki with applications
9. SVN_USER - SVN user to read docker-compose.yml
10. SVN_PASSWORD - SVN password to read docker-compose.yml
11. GITHUB_TOKEN - can be given to avoid API number of requests per IP restrictions
12. DEBUG - Set to "Yes" to enable debug logging
13. DOCKERHUB_USER - user used to interogate image builds on dockerhub
14. DOCKERHUB_PASS - password for the previously defined user
15. WIKI_APPS_PAGE - Redmine wiki apps page
16. WIKI_NODES_PAGE - Redmine wiki nodes page
17. WIKI_PODS_PAGE - Redmine wiki pods page
18. RANCHER2_SERVER_URL - Rancher url (non-main instances)
19. RANCHER2_CLUSTER_ID - Cluster id (non-main instances)
20. RANCHER2_CLUSTER_NAME - Cluster name (non-main instances)
21. RANCHER2_CLUSTERS_TO_MERGE - server url,server name,cluster name1|server url,server name,cluster name2 (main instance)

## Usage

### Will generate the wiki pages in stdout, no redmine necessary

     docker run --rm -e RANCHER_CONFIG="RANCHER-URL,RANCHER-ACCESS-KEY,RANCHER-SECRET-KEY" eeacms/redmine-wikiman

### Will save the wiki pages in redmine

     docker run --rm -e RANCHER_CONFIG="RANCHER-URL,RANCHER-ACCESS-KEY,RANCHER-SECRET-KEY" -e WIKI_SERVER="REDMINE_URL" -e WIKI_APIKEY="REDMINE-KEY" -e WIKI_PROJECT=project -e WIKI_HOSTS_PAGE=RancherHosts -e WIKI_STACKS_PAGE=RancherStacks -e WIKI_CONTAINERS_PAGE=RancherContainers -e DOCKERHUB_USER=user -e DOCKERHUB_PASS=password eeacms/redmine-wikiman

## Scripts

The scripts can be run in python, with the following arguments:
* -v - debug logging
* -n - wiki is not uploaded to redmine

For example:
    python /listcontainers.py -v -n


### listcontainers.py

Updates a redmine wiki page with the list of current docker containers per rancher environment ( including image, stack, status), calculating memory reservation and limit per host/environment/rancher and comparing it with the real memory values.

### listhosts.py

Updates a redmine wiki page with the list of current hosts per rancher environment ( including check_mk link, docker version, OS version), calculating available and used memory percentages.

### liststacks.py

Updates a redmine wiki page with the list of current rancher stacks per environment ( including date created, state and health, catalog, description and tags ).

### listpods.py

Updates a redmine draft wiki page with the list of current pods/containers per rancher2 cluster grouped by nodes ( including namespace, image, status), calculating memory reservation and limit per cluster. The listing is performed on each cluster and a main instance will merge each cluster's generated draft into a final wiki page per environment.

### listnodes.py

Updates a redmine draft wiki page with the list of current nodes per rancher2 cluster ( including check_mk link, taints, docker version, OS version), calculating available and used memory percentages. The listing is performed on each cluster and a main instance will merge each cluster's generated draft into a final wiki page per environment.

### listapps.py

Updates a redmine draft wiki page with the list of current apps per rancher2 cluster grouped by namespaces ( including state, helm chart name, helm chart version, description). The listing is performed on each cluster and a main instance will merge each cluster's generated draft into a final wiki page per environment.

### addimageinfo.py

Updates all wiki subpages for a redmine wiki page, searching for "DeploymentRepoURL" url and extracting docker image information ( docker hub link and github link ) and adding and populating "Source code information" section of the wiki with it.

### applytemplate.py

Updates all factsheet wiki subpages to match the factsheet template. It adds missing mandatory fields and sections, reorders the existing ones, and generates a report grouped by product owner.

The script can detect alternate section titles and normalize them to match the title in the template. This is especially useful when changing a title in the template. Specify them as `*Alternate titles* : title one; title two` (a list delimited by `;`).

Updates all wiki subpages for a redmine wiki page, searching for "Service location" link and extracting it from the current rancher stacks list (created by liststacks.py) and adding the stack link in the field "Rancher Stack URL" to it.


## Development

To make the development easier, you can mount the scripts in a volume and run them:

    docker run -v $(pwd)/src:/src -e RANCHER_CONFIG="RANCHER-URL,RANCHER-ACCESS-KEY,RANCHER-SECRET-KEY" -e WIKI_SERVER="REDMINE_URL" -e WIKI_APIKEY="REDMINE-KEY" -e WIKI_PROJECT=project -e WIKI_HOSTS_PAGE=RancherHosts -e WIKI_STACKS_PAGE=RancherStacks -e WIKI_CONTAINERS_PAGE=RancherContainers eeacms/rancher2redmine sh
    $python /src/listcontainers.py -v
