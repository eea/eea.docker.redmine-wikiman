import json
import urllib, urllib2, os, sys, logging, time
import ConfigParser, getopt
from operator import itemgetter, attrgetter, methodcaller

from redmine import Redmine

def getKey(instance):
    """ Return the key to sort on """
    return instance['name']

class Discover(object):

    def __init__(self, config):
        self.config = config
        self.num_containers = 0
        self.containers = {}
        self.hosts = {}

    def write_page(self, content):
        server = self.config.get('wiki','server')
        apikey = self.config.get('wiki','apikey')
        projectName = self.config.get('wiki','project')
        pageName = self.config.get('wiki','containerpage')
        #pageTitle = self.config.get('wiki','title')
        server = Redmine(server, key=apikey, requests={'verify': True})
        server.wiki_page.update(pageName, project_id=projectName, text=content)

    def write_stdout(self, content):
        #pass
        print content

    def get_operation(self, host, environment, path):
        rancherUrl = self.config.get(host, "stack_url", vars={'env': environment})
        rancherAccessKey = self.config.get(host, "rancher_access_key")
        rancherSecretKey = self.config.get(host, "rancher_secret_key")
        realm = "Enter API access key and secret key as username and password"

        auth_handler = urllib2.HTTPBasicAuthHandler()
        auth_handler.add_password(realm=realm, uri=rancherUrl, user=rancherAccessKey, passwd=rancherSecretKey)
        opener = urllib2.build_opener(auth_handler)
        urllib2.install_opener(opener)
        f = urllib2.urlopen(rancherUrl + path)
        rawdata = f.read()
        f.close()
        return json.loads(rawdata)

    def load_containers(self, server, environment):
        self.containers = {}
        self.num_containers = 0
        structdata = self.get_operation(server, environment, "/containers?limit=1000")
        for instance in structdata['data']:
            imageUuid = instance['imageUuid']
            if imageUuid.startswith("docker:rancher/") and not imageUuid.startswith("docker:rancher/lb-service-haproxy"): continue
            imageUuid = imageUuid[7:]
            containerStruct = self.containers.setdefault(imageUuid, [])
            containerStruct.append(instance)
            self.num_containers = self.num_containers + 1
            #print instance

    def buildgraph(self, content):
        content.append('|_. Image |_. Container |_. State |_. Reservation |_. Limit |')
        for imageName, containers in sorted(self.containers.items()):
            #content.append('h3. {}\n'.format(imageName))
            for container in sorted(containers, key=itemgetter('name')):
#               if container['imageUuid'].startswith("docker:rancher/"): continue
                contName = container['name']
                memoryRes = container.get('memoryReservation', 0)
                if memoryRes is None: memoryRes = 0
                memoryRes = memoryRes / 1048576

                memoryLim = container.get('memory', 0)
                if memoryLim is None: memoryLim = 0
                memoryLim = memoryLim / 1048576
                content.append('| {} | {} | {} |>. {} |>. {} |'.format(imageName, contName, container['state'], memoryRes, memoryLim))
#           content.append('\n')

if __name__ == '__main__':
    config = ConfigParser.SafeConfigParser()
    config.read('rancher.cfg')
    dryrun = False
    try:
        opts, args = getopt.getopt(sys.argv[1:], "vn")
    except getopt.GetoptError, err:
        sys.exit(2)
    for o, a in opts:
        if o == "-v":
            logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)
        if o == "-n":
            dryrun = True
    if len(args) > 0:
        servers = args
    else:
        servers = config.get("general", "hosts").split()
    pageTitle = config.get('wiki','containertitle')
    content = []
    content.append('{{>toc}}\n\n')
    content.append('h1. ' + pageTitle + '\n\n')
    content.append('Automatically discovered on ' + time.strftime('%d %B %Y') + '. _Do not update this page manually._')

    disc = Discover(config)
    for server in servers:
        logging.info(server)
        rancherUrl = config.get(server, "rancher_url", vars={'host': server})
        envstruct = disc.get_operation(server, "", "")
        for project in sorted(envstruct['data'], key=getKey):

            if project['state'] != 'active': continue
            environment = project['id']
            logging.info("Environment: %s", environment)
            envLabel = project['name']
            content.append('\nh2. {}\n'.format(envLabel))
            content.append('\n')
            disc.load_containers(server, environment)
            disc.buildgraph(content)
            content.append('\n')
            content.append('Number of containers: {}\n'.format(disc.num_containers))
    if dryrun:
        disc.write_stdout("\n".join(content))
    else:
        disc.write_page("\n".join(content))
    #print "Done"
