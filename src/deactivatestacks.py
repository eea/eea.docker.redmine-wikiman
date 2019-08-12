import json
import urllib, urllib2, os, sys, logging
import ConfigParser, getopt

def get_operation(config, environment, url):
    rancherUrl = config.get(environment, "rancher_url", vars={'env': environment})
    rancherAccessKey = config.get(environment, "rancher_access_key")
    rancherSecretKey = config.get(environment, "rancher_secret_key")
    realm = "Enter API access key and secret key as username and password"

    auth_handler = urllib2.HTTPBasicAuthHandler()
    auth_handler.add_password(realm=realm, uri=rancherUrl, user=rancherAccessKey, passwd=rancherSecretKey)
    opener = urllib2.build_opener(auth_handler)
    urllib2.install_opener(opener)
    f = urllib2.urlopen(url)
    rawdata = f.read()
    f.close()
    return json.loads(rawdata)

def post_operation(config, environment, url):
    rancherUrl = config.get(environment, "rancher_url", vars={'env': environment})
    rancherAccessKey = config.get(environment, "rancher_access_key")
    rancherSecretKey = config.get(environment, "rancher_secret_key")
    realm = "Enter API access key and secret key as username and password"

    auth_handler = urllib2.HTTPBasicAuthHandler()
    auth_handler.add_password(realm=realm, uri=rancherUrl, user=rancherAccessKey, passwd=rancherSecretKey)
    opener = urllib2.build_opener(auth_handler)
    urllib2.install_opener(opener)
    try:
        f = urllib2.urlopen(url, "")
        rawdata = f.read()
        f.close()
    except:
        return ""
    return json.loads(rawdata)

if __name__ == '__main__':
    dryrun = False
    config = ConfigParser.SafeConfigParser()
    config.read('rancher.cfg')
    try:
        opts, args = getopt.getopt(sys.argv[1:], "vn")
    except getopt.GetoptError, err:
        sys.exit(2)
    for o, a in opts:
        if o == "-v":
            logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)
        if o == "-n":
            dryrun = True

    environments = config.get("general", "deactivation").split()

    for environment in environments:
        rancherUrl = config.get(environment, "rancher_url", vars={'env': environment})
        structdata = get_operation(config, environment, rancherUrl + "/environments")

        keeplist = config.get(environment, "keep").split()

        for instance in structdata['data']:
            actions = instance['actions']
            name = instance['name']
            healthState = instance['healthState']
            logging.debug("Found %s, health: %s, state: %s", name, healthState, instance['state'])
            if name in keeplist:
                logging.info("Keeping %s", name)
                continue
            if healthState in ("healthy", "degraded"):
                #print "KILL", actions['deactivateservices']
                logging.warning("Deactivating %s: %s", name, actions['deactivateservices'])
                if dryrun == False:
                    post_operation(config, environment, actions['deactivateservices'])
                continue

