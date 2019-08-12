import json
import urllib, urllib2, os, sys, logging
import ConfigParser, getopt

from redmine import Redmine

class Discover(object):

    containers = {}
    hosts = {}

    def __init__(self, config):
        self.config = config

    def write_page(self, content):
        server = self.config.get('wiki','server')
        apikey = self.config.get('wiki','apikey')
        projectName = self.config.get('wiki','project')
        pageName = self.config.get('wiki','page')
        #pageTitle = self.config.get('wiki','title')
        server = Redmine(server, key=apikey, requests={'verify': True})
        server.wiki_page.update(pageName, project_id=projectName, text=content)

    def write_stdout(self, content):
        #pass
        print content

    def get_operation(self, environment, url):
        rancherUrl = self.config.get(environment, "rancher_url")
        rancherAccessKey = self.config.get(environment, "rancher_access_key")
        rancherSecretKey = self.config.get(environment, "rancher_secret_key")
        realm = "Enter API access key and secret key as username and password"

        auth_handler = urllib2.HTTPBasicAuthHandler()
        auth_handler.add_password(realm=realm, uri=rancherUrl, user=rancherAccessKey, passwd=rancherSecretKey)
        opener = urllib2.build_opener(auth_handler)
        urllib2.install_opener(opener)
        f = urllib2.urlopen(url)
        rawdata = f.read()
        f.close()
        return json.loads(rawdata)

    def load_containers(self, environment):
        rancherUrl = config.get(environment, "rancher_url")
        structdata = self.get_operation(environment, rancherUrl + "/containers?limit=1000")
        for instance in structdata['data']:
            if 'hostId' not in instance:
                continue
            hostId = instance['hostId']
            if hostId is None: continue
            instanceLinks = instance['links']['instanceLinks']
            if instance['name'] == 'Network Agent': continue
            hostStruct = self.containers[hostId]
            hostStruct.append(instance)

    def load_hosts(self, environment):
        rancherUrl = config.get(environment, "rancher_url")
        structdata = self.get_operation(environment, rancherUrl + "/hosts")
        for instance in structdata['data']:
            name = instance['hostname']
            hostId = instance['id']
            self.hosts[hostId] = name
            self.containers[hostId] = []
        #print self.hosts

    def buildgraph(self, content):
        for hostId, name in self.hosts.items():
            content.append('  subgraph cluster_{} {{ label = "{}"'.format(hostId, name))
            for container in self.containers[hostId]:
                content.append('    "{}" [label="{}"]'.format(container['id'], container['name']))
            content.append('  }')

if __name__ == '__main__':
    config = ConfigParser.SafeConfigParser()
    config.read('rancher.cfg')
    try:
        opts, args = getopt.getopt(sys.argv[1:], "v")
    except getopt.GetoptError, err:
        sys.exit(2)
    for o, a in opts:
        if o == "-v":
            logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)
    if len(args) > 0:
        environments = args
    else:
        environments = config.get("general", "digraph").split()
    #pageTitle = config.get('wiki','title')
    content = []
    #content.append('h1. ' + pageTitle + '\n\n')
    for environment in environments:
        #print environment
        content.append('graph {} {{\n'.format(environment))
        content.append('  rankdir = LR')
        disc = Discover(config)
        disc.load_hosts(environment)
        disc.load_containers(environment)
        disc.buildgraph(content)
        content.append('}')
    disc.write_stdout("\n".join(content))
    #print "Done"
