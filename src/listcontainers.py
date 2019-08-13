import json
import urllib.request, urllib.parse, urllib.error, urllib.request, urllib.error, urllib.parse, os, sys, logging, time
import getopt
from operator import itemgetter, attrgetter, methodcaller
from urllib.parse import urlparse

from redminelib import Redmine

def getKey(instance):
    """ Return the key to sort on """
    return instance['name']

class Discover(object):

    def __init__(self):
        self.num_containers = 0
        self.containers = {}
        self.hosts = {}

    def write_page(self, content):
        server = os.getenv('wiki_server','')
        apikey = os.getenv('wiki_apikey','')
        projectName = os.getenv('wiki_project','')
        pageName = os.getenv('wiki_containers_page','')
        server = Redmine(server, key=apikey, requests={'verify': True})
        server.wiki_page.update(pageName, project_id=projectName, text=content)


    def write_stdout(self, content):
        #pass
        print(content)

    def get_operation(self,  rancherUrl, rancherAccessKey, rancherSecretKey, url):
        realm = "Enter API access key and secret key as username and password"

        auth_handler = urllib.request.HTTPBasicAuthHandler()
        auth_handler.add_password(realm=realm, uri=rancherUrl, user=rancherAccessKey, passwd=rancherSecretKey)
        opener = urllib.request.build_opener(auth_handler)
        urllib.request.install_opener(opener)
        f = urllib.request.urlopen(url)
        rawdata = f.read()
        f.close()
        return json.loads(rawdata)

    def load_containers(self, rancherUrl, rancherAccessKey, rancherSecretKey, url):
        self.containers = {}
        self.num_containers = 0
        structdata = self.get_operation(rancherUrl, rancherAccessKey, rancherSecretKey, url + "/containers?limit=1000")
        for instance in structdata['data']:
            imageUuid = instance['imageUuid']
            if imageUuid.startswith("docker:rancher/") and not imageUuid.startswith("docker:rancher/lb-service-haproxy"): continue
            imageUuid = imageUuid[7:]
            containerStruct = self.containers.setdefault(imageUuid, [])
            containerStruct.append(instance)
            self.num_containers = self.num_containers + 1
            #print instance

    def buildgraph(self, content):
        content.append('|_. Image |_. Container |_. Stack |_. State |_. Reservation |_. Limit |')
        for imageName, containers in sorted(self.containers.items()):
            #content.append('h3. {}\n'.format(imageName))
            for container in sorted(containers, key=itemgetter('name')):
#               if container['imageUuid'].startswith("docker:rancher/"): continue
                contName = container['name']
                try:
                    stackName = container['labels']["io.rancher.stack.name"]
                except KeyError:
                    stackName = ""
                memoryRes = container.get('memoryReservation', 0)
                if memoryRes is None: memoryRes = 0
                memoryRes = memoryRes / 1048576

                memoryLim = container.get('memory', 0)
                if memoryLim is None: memoryLim = 0
                memoryLim = memoryLim / 1048576
                content.append('| {} | {} | {} | {} |>. {} |>. {} |'.format(imageName, contName, stackName, container['state'], memoryRes, memoryLim))
#           content.append('\n')

if __name__ == '__main__':
    dryrun = False
    logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.INFO)
    try:
        opts, args = getopt.getopt(sys.argv[1:], "vn")
    except getopt.GetoptError as err:
        sys.exit(2)
    for o, a in opts:
        if o == "-v":
            logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)
        if o == "-n":
            dryrun = True
    
    pageTitle = os.getenv('wiki_containerspagetitle', 'Rancher Containers')

    content = []
    content.append('{{>toc}}\n\n')
    content.append('h1. ' + pageTitle + '\n\n')
    content.append('Automatically discovered on ' + time.strftime('%d %B %Y') + '. _Do not update this page manually._')

    disc = Discover()
    rancher_configs = os.getenv('rancher_config')

    for rancher_config in rancher_configs.split():
        
        rancher_configuration = rancher_config.split(",")
        
        rancherUrl = rancher_configuration[0]
        logging.info(rancherUrl)
        rancherApiUrl = rancherUrl + "/v2-beta"
        rancherAccessKey = rancher_configuration[1]
        rancherSecretKey = rancher_configuration[2]
        
        content.append('\nh2. {}\n'.format(urlparse(rancherUrl).netloc.upper()))

        envstruct = disc.get_operation( rancherApiUrl, rancherAccessKey, rancherSecretKey, rancherApiUrl+"/projects")

        for project in sorted(envstruct['data'], key=getKey):

            if project['state'] != 'active': continue
            environment = project['id']
            envURL = rancherUrl + "/env/" + environment
            logging.info("Retrieving %s - %s", environment, project['name'])
            envLabel = project['name']
            content.append('\nh3. "{}":{}\n'.format(envLabel,envURL))
            content.append('\n')
            disc.load_containers(rancherApiUrl, rancherAccessKey, rancherSecretKey, rancherApiUrl+"/projects/"+environment)
            disc.buildgraph(content)
            content.append('\n')
            content.append('Number of containers: {}\n'.format(disc.num_containers))
    if dryrun:
        disc.write_stdout("\n".join(content))
    else:
        disc.write_page("\n".join(content))
    #print "Done"