from redminelib import Redmine
import re
import sys


server = os.getenv('WIKI_SERVER','')
apikey = os.getenv('WIKI_APIKEY','')
projectName = os.getenv('WIKI_PROJECT','')
pageName = os.getenv('WIKI_CONTAINERS_PAGE','')
stackwiki = os.getenv('STACK_PAGE','Rancher_stacks')

server = Redmine(server, key=apikey, requests={'verify': True})
project = server.project.get(projectName)
stack_wiki = server.wiki_page.get(stackwiki, project_id=projectName)
stacks = stack_wiki.text.splitlines()

list = []
for page in project.wiki_pages:
  if ( str(getattr(page, 'parent', None)) == pageName ):
      list.append(page)

list2 = []
for page in project.wiki_pages:
  for child in list:
    if ( str(getattr(page, 'parent', None)) == str(child) ):
        list2.append(page)
     
list.extend(list2)

list_pages = []
for page in list:
   if ( getattr(page, 'text', None).find('Service location:') > 0 ):
     list_pages.append(page)

for page in list_pages:
  print "Starting with " + str(page)
  lines = page.text.replace('\n<div id="wiki_extentions_header">\n\n{{last_updated_at}} _by_ {{last_updated_by}}\n\n</div>\n\n','').splitlines()
  next_line = ''
  text = ''
  something_changed = ''
  for line in lines:
    if next_line:
      if 'Rancher Stack URL:' in line:
        text = text + next_line + '\n'
        if line != next_line:
          something_changed = 'yes'
      else:
        text = text + next_line + '\n' + line + '\n'
        something_changed = 'yes'
      next_line = ''
    else:
      text = text + line + '\n'
    if 'Service location:' in line:
      url = line.replace('Service location:','').strip().lower().strip('/')
      #print '\n'
      print url
      stack = ""
      if not url:
        print str(page) + " Could not extract url"
        continue
      # check normal url
      url_no_protocol = url.replace('http://','').replace('https://','')
      regex = re.compile(r' (https?://)?{}/?[^a-z/]'.format(url_no_protocol.replace('.','\.')), re.IGNORECASE)
      stack = filter(regex.search, stacks)
      if not stack:
        stack_name = url_no_protocol.replace('.europa.eu','').replace('.','-')
        stack = [x for x in stacks if '|"' + stack_name + '":' in x.lower()]
      if not stack:
        print "Could not find stack for url " + url
        continue
      print url
      print stack
      next_line = "Rancher Stack URL: "
      regex = re.compile(r'^\|(".+?":.+?) \|.*$')
      for st in stack:
        stack_url = regex.match(str(st)).group(1) 
        next_line = next_line + stack_url + ', '
      next_line = next_line.strip().strip(',')
      print next_line
  if something_changed:
    print "Saving page"
    page.text = text
    page.save()
