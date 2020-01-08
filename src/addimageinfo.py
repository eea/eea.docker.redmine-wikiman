from redminelib import Redmine
import requests
import datetime
import re
import svn.remote
import sys
import os
import logging
import getopt

wiki_server = os.getenv('WIKI_SERVER', '')
wiki_apikey = os.getenv('WIKI_APIKEY', '')
projectName = os.getenv('WIKI_PROJECT', '')
pageName = os.getenv('WIKI_PAGE', 'Applications')
svnuser = os.getenv('SVN_USER', '')
svnpassword = os.getenv('SVN_PASSWORD', '')
github_token = os.getenv('GITHUB_TOKEN', '')


# Github authorization
authorization_header = {}
raw_header = {'Accept': 'application/vnd.github.VERSION.raw'}
if github_token:
    logging.info("Received GitHub token value, will be using it to read github repos")
    authorization_header = {'Authorization': 'bearer ' + github_token}
    raw_header = {'Accept': 'application/vnd.github.VERSION.raw', 'Authorization': 'bearer ' + github_token}


def get_dockerfile(url):
    logging.debug("Deployment url " + url)
    try:
      if 'https://github.com/' in url:
        api = url.replace('https://github.com/',
                          'https://api.github.com/repos/').replace('tree/master',
                                                                   'contents')
        response = requests.get(api, headers=authorization_header)
        if response.status_code != 200:
          logging.debug(response.json())
          logging.warning("There was a problem with the github api response")
          return
        filter_dirs = [x for x in response.json() if x['type'] == 'dir']
        biggest = str(max(filter_dirs, key=lambda x: int(x['name']))['name'])
        response2 = requests.get(api.strip('/') + "/" + biggest, headers=authorization_header)
        filter_dc = [x for x in response2.json(
        ) if 'docker-compose' in str(x['name']).lower()]
        response3 = requests.get(str(filter_dc[0]['url']), headers=raw_header)
        return response3.text

      if 'https://eeasvn.eea.europa.eu/' in url:
        r = svn.remote.RemoteClient(url, username=svnuser, password=svnpassword)
        r.info()
        for x in r.list():
          if str(x).lower() == 'docker-compose.yml':
            return r.cat(x).decode('utf-8')
        for rel_path, e in r.list_recursive():
          if str(e['name']).lower() == 'docker-compose.yml':
            return r.cat(rel_path + '/' + e['name']).decode('utf-8')

    except BaseException:
      logging.warning(
        "There was a problem accessing the DeploymentRepoURL docker-compose.yml")
      logging.error(sys.exc_info())


def get_docker_images(urls):
    docker_images = {}
    for url in urls:
      dockerfile = get_dockerfile(url)

      if not dockerfile:
        logging.warning("No docker-compose.yml file found, skipping the page")
        return {}
        break

      lines = dockerfile.splitlines()
      images = [x for x in lines if ' image: ' in x]
      for image in images:
        name = image.strip().split(' ')[1].strip('"').strip("'")
        # print name
        if name not in docker_images:
          docker_image = name.split(':')[0]
          response4 = requests.get(
              "https://hub.docker.com/api/build/v1/source/?image=" +
              docker_image)
          github_url = ''
          dockerhub_url = ''
          if docker_image.find('/') > 0:
            dockerhub_url = 'https://hub.docker.com/r/' + docker_image
            # print dockerhub_url
            if response4.status_code == 200 and response4.json().get('objects'):
              github_url = 'https://github.com/' + \
                response4.json()['objects'][0]['owner'] + '/' + \
                  response4.json()['objects'][0]['repository']
              # print github_url
          else:
            dockerhub_url = 'https://hub.docker.com/r/_/' + docker_image
            # print dockerhub_url
          docker_images[name] = [github_url, dockerhub_url]
      # print docker_images

    return docker_images


def main():
    try:
      opts, args = getopt.getopt(sys.argv[1:], "dvn")
    except getopt.GetoptError:
      sys.exit(2)

    dryrun = False
    debug = False

    for o, a in opts:
      if o == "-v":
        debug = True
      if o == "-n":
        dryrun = True

    if debug:
      logging.basicConfig(format='%(levelname)s: %(message)s', level=logging.DEBUG)
    else:
      logging.basicConfig(format='%(levelname)s: %(message)s', level=logging.INFO)


    logging.info("START")


    server = Redmine(wiki_server, key=wiki_apikey, requests={'verify': True})

    list1 = []

    try:
      project = server.project.get(projectName)
      for page in project.wiki_pages:
        if (str(getattr(page, 'parent', None)) == pageName):
          list1.append(page)
      list2 = []
      for page in project.wiki_pages:
        for child in list1:
          if (str(getattr(page, 'parent', None)) == str(child)):
            list2.append(page)
      list1.extend(list2)
    except BaseException:
      logging.error("There was a problem reading from taskman wiki")
      logging.error(sys.exc_info())
      sys.exit(0)

    list_pages = []
    for page in list1:
      if (getattr(page, 'text', None).lower().find('deploymentrepourl:') > 0):
        list_pages.append(page)

    for page in list_pages:
      logging.info("Starting with " + str(page))
      lines = page.text.splitlines()
      repos = [x for x in lines if 'deploymentrepourl:' in x.lower()]
      urls = []
      for line in repos:
        if line.strip().lower().index('deploymentrepourl:') > 0:
          continue
        urls.append(line.lower().replace('deploymentrepourl:', '').strip())

      docker_images = get_docker_images(urls)

      if not docker_images:
        logging.debug("No docker images extracted, will continue")
        continue

      # text = "\nh2. Source code information\n\n"
      comment = "\n\n??This section of the wiki was generated automatically from the DeploymentRepoURL on " + \
          datetime.date.today().strftime("%Y-%m-%d") + \
          ", please don't edit it manually.??\n\n"
      text = ""
      # print sorted(docker_images)
      for name in sorted(docker_images):
        text = text + '* *"' + name + '":' + docker_images[name][1] + '*'
        if docker_images[name][0]:
          text = text + ' | "Source code":' + docker_images[name][0]
        text = text + "\n"
      text = text + "\n"
      new_text = ""
      pagetext = page.text + "h2. Temp\n"
      section = re.findall(
          r'(?<=h2. Source code information).+?(?=h2)',
          pagetext,
          re.DOTALL)
      if section:
        images_list = re.findall(r'(?<=\*).+?(?=$)', section[0], re.DOTALL)
        replace_section = ""
        if images_list:
          # print "found list of images"
          # print "-------------------------------"
          #  print images_list[0]
          new_images_list = re.findall(
              r'(?<=\*).+?(?=$)', comment + text, re.DOTALL)
        # print "-------------------------------"
        #  print new_images_list[0]
        #  print "-------------------------------"

          if new_images_list[0].rstrip() == images_list[0].rstrip():
            logging.debug("Nothing to update")
          else:
            replace_section = "yes"
        else:
          replace_section = "yes"

        if replace_section:
          logging.debug("Replaced the source code information text")
          new_text = page.text.replace(section[0], comment + text)
      else:
        new_text = page.text + "\n\nh2. Source code information" + comment + text
        logging.debug("Added new section")

      if new_text:
        new_text = new_text.replace(
            '\n<div id="wiki_extentions_header">\n\n{{last_updated_at}} _by_ {{last_updated_by}}\n\n</div>\n\n',
            '')
        if dryrun:
          logging.info("Changes found, but they won't be saved")
          logging.debug(new_text)
        else:
          logging.info("Saving page")
          page.text = new_text
          page.save()

    logging.info("DONE")


if __name__ == '__main__':
    main()
