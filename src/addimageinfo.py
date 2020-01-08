import requests
import datetime
import svn.remote
import sys
import os
import logging

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


def generate_images_text(docker_images):
    text = ""
    # print sorted(docker_images)
    for name in sorted(docker_images):
      text = text + '* *"' + name + '":' + docker_images[name][1] + '*'
      if docker_images[name][0]:
        text = text + ' | "Source code":' + docker_images[name][0]
      text = text + "\n"
    text = text + "\n"
    return text
