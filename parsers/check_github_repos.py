#!/usr/bin/env python
"""
This script uses the GitHub API (via PyGithub) to list all of a user's
repositories (optionally including forks), searches each repository for
RepoStatus information in ``repostatus.org``, ``.repostatus.org``, or any file
in the root of the repository starting with ``readme`` (case-insensitive),
and outputs a listing of repos and their statuses.

============
Requirements
============

* Python 2.7 or newer.
* pygithub - `pip install pygithub` <https://github.com/jacquev6/PyGithub>
* requests - `pip install requests`

You'll need to set your GitHub API token in your git config;
use `git config --global github.token <your token>` to set it
if not already present.

=========
Copyright
=========

Copyright 2014-2022 Jason Antman <jason@jasonantman.com> <http://www.jasonantman.com>
Free for any use provided that patches are submitted back to me.

The latest version of this script can be found at:
https://github.com/jantman/repostatus.org/blob/master/parsers/repostatusorg_list_repo_status.py

=========
CHANGELOG
=========

2018-04-01 jantman:
- Give user some help if non-standard library modules can't be imported
- Make RepoStatusOrg_GitHub_Checker a new-style class
- Update summary at top of this docstring
- Add -F/--fail-on-unknown option
- Python3 fixes

2016-05-18 jantman:
- add links to repo in HTML output

2016-05-17 jantman:
- add JSON and HTML output options

2014-12-25 jantman:
- initial script
"""

import sys
import argparse
import logging
import re
import subprocess
from base64 import b64decode
import json
from datetime import datetime

try:
    import requests
except ImportError:
    sys.stderr.write(
        'ERROR importing "requests". If it is not installed, please '
        '"pip install requests"\n'
    )
    raise
try:
    from github import Github
except ImportError:
    sys.stderr.write(
        'ERROR importing "github". If it is not installed, please '
        '"pip install pygithub"\n'
    )
    raise

FORMAT = "[%(levelname)s %(filename)s:%(lineno)s - %(funcName)20s() ] %(message)s"
logging.basicConfig(level=logging.INFO, format=FORMAT)


class RepoStatusOrg_GitHub_Checker(object):
    """ check a user's GitHub repos for repostatus.org status identifiers """

    readme_re = re.compile(r'^readme.*$', flags=re.I)
    url_re = re.compile(r'http[s]?:\/\/.*repostatus\.org\/badges\/(.+)\/(.+)\.svg', flags=re.I)

    def __init__(self, verbose=False):
        self.logger = logging.getLogger(self.__class__.__name__)
        if verbose:
            self.logger.setLevel(logging.DEBUG)
        # try to get GitHub credentials
        try:
            token = subprocess.check_output(['git', 'config', '--global', 'github.token']).strip()
            if isinstance(token, type(b'')):
                token = token.decode()
            self.logger.debug("got github token: {t}".format(t=token))
        except subprocess.CalledProcessError:
            self.logger.error("ERROR: no github token found. Set 'git config --global github.token' to your API token.")
            raise SystemExit(1)
        self.logger.debug("connecting to GitHub API")
        self.g = Github(login_or_token=token)

    def check(self, github_user, include_forks=False):
        """
        Check all repositories of a given GitHub user (or organization) for
        repostatus.org identifiers.

        returns a dictionary of repo name to status name (or None if no status found)

        :param github_user: github user or organization to check repos for, or None for logged in user
        :type github_user: string
        :rtype: dict
        """
        res = {}
        if github_user is None:
            github_user = self.g.get_user().login
        self.username = github_user
        self.logger.debug("checking repos for user {u}".format(u=github_user))
        user = self.g.get_user(github_user)
        if user.type == 'Organization':
            self.logger.debug("user is an Organization; using organization instead")
            user = self.g.get_organization(user.login)
        self.logger.debug("user has {r} public repos and {p} owned private repos".format(r=user.public_repos, p=user.owned_private_repos))
        repos = user.get_repos()
        count = 0
        forks = 0
        for repo in repos:
            if repo.fork and not include_forks:
                self.logger.debug("ignoring fork: {r}".format(r=repo.name))
                forks += 1
                continue
            count += 1
            self.logger.debug("checking repo {r}".format(r=repo.name))
            candidates = self._find_candidate_files(repo)
            self.logger.debug("found {c} candidate files".format(c=len(candidates)))
            if len(candidates) == 0:
                continue
            status = self._find_status_for_files(repo, candidates)
            if status is not None:
                self.logger.debug("found status {s} for repo {r}".format(s=status, r=repo.name))
                res[repo.name] = status
            else:
                self.logger.debug("found no status for repo {r}".format(r=repo.name))
                res[repo.name] = None
        self.logger.debug("checked {c} repos for user; ignored {f} forks".format(c=count, f=forks))
        return res

    def _find_status_for_files(self, repo, flist):
        """
        Given a list of files to search, returns the repostatus.org version
        and status name of the first matching status identifier URL found;
        searches the files in list order. Returns None if no match found

        :param repo: repository to check
        :type repo: github.Repository.Repository
        :param flist: list of files to search through, in order
        :type flist: list of strings (file paths)
        :rtype: 2-tuple (version, status name) or None
        """
        for f in flist:
            content = repo.get_contents(f)
            s = ''
            if content.encoding == 'base64':
                s = b64decode(content.content)
                if isinstance(s, type(b'')):
                    s = s.decode()
            else:
                self.logger.error(
                    "unknown encoding '%s' on file %s in repository %s",
                    content.encoding, content.path, repo.name
                )
            res = self.url_re.search(s)
            if res is not None:
                self.logger.debug("Match found in {f}: {u}".format(f=content.path, u=res.group(0)))
                return (res.group(1), res.group(2))
        return None

    def _find_candidate_files(self, repo):
        """
        Return a list of all files in the top directory/path of the repository
        which should be examined for a repostatus identifier.
        List is in the order they should be checked.

        :param repo: repository to check
        :type repo: github.Repository.Repository
        :rtype: list of string filenames
        """
        files = []
        for x in repo.get_contents('/'):
            if x.type != 'file':
                continue
            files.append(x.name)
        candidates = []
        # sort files lexicographically
        for fname in sorted(files, key=lambda x: x.lower()):
            if self.readme_re.match(fname):
                candidates.append(fname)
        if '.repostatus.org' in files:
            candidates.append('.repostatus.org')
        if 'repostatus.org' in files:
            candidates.append('repostatus.org')
        return candidates


def parse_args(argv):
    """
    parse command line arguments/options
    """
    p = argparse.ArgumentParser(description='repostatus.org GitHub parser')
    p.add_argument('-v', '--verbose', dest='verbose', action='store_true', default=False,
                   help='verbose output (internal debugging).')
    p.add_argument('-u', '--user', dest='user', type=str, default=None,
                   help='GitHub user or organization to check repos for; defaults to current user')
    p.add_argument('-f', '--forks', dest='forks', action='store_true', default=False,
                   help='also include forks')
    p.add_argument('-o', '--output-format', dest='format', action='store',
                   choices=['text', 'json', 'html'],
                   default='text', help='output format - (text|json|html) - default "text"')
    p.add_argument('-F', '--fail-on-unknown', dest='fail_on_unknown',
                   action='store_true', default=False,
                   help='exit 1 if any repos have an unknown status')
    args = p.parse_args(argv)
    return args


def htmlout(output, username):
    out = """
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <meta http-equiv="Content-Type" content="text/html; charset=windows-1252">
    <title>repostatus.org parse results for {user}</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/jquery/2.2.3/jquery.min.js" type="text/javascript"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/jquery.tablesorter/2.26.1/js/jquery.tablesorter.js" type="text/javascript"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/jquery.tablesorter/2.26.1/js/jquery.tablesorter.widgets.js" type="text/javascript"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/jquery.tablesorter/2.26.1/css/theme.default.min.css" />
  </head>
  <body>
    <table id="myTable" class="tablesorter">
      <thead>
        <th>Repo Name</th>
        <th>Status</th>
      </thead>
      <tbody>
{tbody}
      </tbody>
    </table>
    <p><em>Generated by repostatus.org check_github_repos.py parser at {dt}</em></p>
{script}
  </body>
</html>
"""
    script = """
    <script type="text/javascript">
      $(document).ready(function()
        {
          $("#myTable").tablesorter();
        }
      );
    </script>
"""
    tbody = ''
    for repo in sorted(output):
        href = 'https://github.com/%s/%s' % (username, repo)
        tbody += "        <tr><td><a href=\"%s\">%s</a></td><td>%s</td></tr>\n" % (
            href, repo, output[repo]
        )
    dt = datetime.now().isoformat()
    out = out.format(user=username, dt=dt, tbody=tbody, script=script)
    return out


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    # initialize the class
    checker = RepoStatusOrg_GitHub_Checker(verbose=args.verbose)
    # run the check
    results = checker.check(args.user, include_forks=args.forks)
    total = 0
    unknown = []
    output = {}
    maxlen = 0
    for repo in results:
        if results[repo] is None:
            s = 'UNKNOWN'
            unknown.append(repo)
        else:
            s = results[repo][1]
        total += 1
        output[repo] = s
        if len(repo) > maxlen:
            maxlen = len(repo)
    if args.format == 'html':
        print(htmlout(output, checker.username))
    elif args.format == 'json':
        print(json.dumps(output))
    else:
        # text
        fs = '{:<%d}   {}' % ( maxlen + 1 )
        for repo in sorted(output):
            print(fs.format(repo, output[repo]))
    checker.logger.info(
        "Found %d repos, %d with unknown status", total, len(unknown)
    )
    if len(unknown) > 0:
        checker.logger.info('Unknown repos: %s', unknown)
        if args.fail_on_unknown:
            raise SystemExit(1)
