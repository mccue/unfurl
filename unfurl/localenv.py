"""
Classes for managing the local environment.

Repositories can optionally be organized into projects that have a local configuration.

By convention, the "home" project defines a localhost instance and adds it to its context.
"""
import os
import os.path

import six
from .repo import Repo, normalizeGitUrl, findGitRepo
from .util import UnfurlError
from .merge import mergeDicts
from .yamlloader import YamlConfig, makeVaultLib
from . import DefaultNames, getHomeConfigPath
from six.moves.urllib.parse import urlparse


class Project(object):
    """
  A Unfurl project is a folder that contains at least a local configuration file (unfurl.yaml),
  one or more ensemble.yaml files which maybe optionally organized into one or more git repositories.
  """

    def __init__(self, path, homeProject=None):
        assert isinstance(path, six.string_types), path
        parentConfig = homeProject and homeProject.localConfig or None
        self.projectRoot = os.path.abspath(os.path.dirname(path))
        if os.path.exists(path):
            self.localConfig = LocalConfig(path, parentConfig)
        else:
            self.localConfig = LocalConfig(parentConfig=parentConfig)

        self.workingDirs = Repo.findGitWorkingDirs(self.projectRoot)
        # the project repo if it exists manages the project config (unfurl.yaml)
        projectRoot = self.projectRoot
        if projectRoot in self.workingDirs:
            self.projectRepo = self.workingDirs[projectRoot][1]
        else:
            # project maybe part of a containing repo (if created with --existing option)
            repo = Repo.findContainingRepo(self.projectRoot)
            # make sure projectroot isn't excluded from the containing repo
            if repo and not repo.isPathExcluded(self.projectRoot):
                self.projectRepo = repo
                self.workingDirs[repo.workingDir] = (repo.url, repo)
            else:
                self.projectRepo = None

        if self.projectRepo:
            for dir in self.projectRepo.findExcludedDirs(self.projectRoot):
                # look for repos that might be in its
                if projectRoot in dir and os.path.isdir(dir):
                    Repo.updateGitWorkingDirs(self.workingDirs, dir, os.listdir(dir))

    @staticmethod
    def normalizePath(path):
        path = os.path.abspath(path)
        if not os.path.exists(path):
            isdir = not path.endswith(".yml") and not path.endswith(".yaml")
        else:
            isdir = os.path.isdir(path)

        if isdir:
            return os.path.join(path, DefaultNames.LocalConfig)
        else:
            return path

    @staticmethod
    def findPath(testPath):
        """
    Walk parents looking for unfurl.yaml
    """
        current = os.path.abspath(testPath)
        while current and current != os.sep:
            test = os.path.join(current, DefaultNames.LocalConfig)
            if os.path.exists(test):
                return test
            current = os.path.dirname(current)
        return None

    @property
    def venv(self):
        venv = os.path.join(self.projectRoot, ".venv")
        if os.path.isdir(venv):
            return venv
        return None

    def getRepos(self):
        repos = [repo for (gitUrl, repo) in self.workingDirs.values()]
        if self.projectRepo and self.projectRepo not in repos:
            repos.append(self.projectRepo)
        return repos

    def findDefaultInstanceManifest(self):
        fullPath = self.localConfig.getDefaultManifestPath()
        if fullPath:
            if not os.path.exists(fullPath):
                raise UnfurlError(
                    "The default ensemble found in %s does not exist: %s"
                    % (self.localConfig.config.path, os.path.abspath(fullPath))
                )
            return fullPath
        else:
            # no manifest specified in the project config so check the default locations
            fullPath = os.path.join(
                self.projectRoot, DefaultNames.EnsembleDirectory, DefaultNames.Ensemble
            )
            if os.path.exists(fullPath):
                return fullPath
            fullPath2 = os.path.join(self.projectRoot, DefaultNames.Ensemble)
            if os.path.exists(fullPath2):
                return fullPath2
            raise UnfurlError(
                'The can not find an ensemble in a default location: "%s" or "%s"'
                % (fullPath, fullPath2)
            )

    def isPathInProject(self, path):
        # better? os.path.relpath(sourceRoot, destDir).startswith(".." + os.sep)
        return (
            os.path.abspath(self.projectRoot) + os.sep in os.path.abspath(path) + os.sep
        )

    def _createPathForGitRepo(self, gitUrl):
        parts = urlparse(gitUrl)
        if parts.scheme == "git-local":
            # e.g. extract spec from git-local://0cfeee6571c4276ce1a63dc37aa8cbf8b8085d60:spec
            name = parts.netloc.partition(":")[1]
        else:
            # e.g. extract tosca-parser from https://github.com/onecommons/tosca-parser.git
            name = os.path.splitext(os.path.basename(parts.path[1:] or parts.netloc))[0]

        assert not name.endswith(".git"), name
        return self.getUniquePath(name)

    def getUniquePath(self, name):
        basename = name
        counter = 1
        while os.path.exists(os.path.join(self.projectRoot, name)):
            name = basename + str(counter)
            counter += 1
        return os.path.join(self.projectRoot, name)

    def findGitRepo(self, repoURL, revision=None):
        candidate = None
        for dir, (url, repo) in self.workingDirs.items():
            if repoURL.startswith("git-local://"):
                initialCommit = urlparse(repoURL).netloc.partition(":")[0]
                match = initialCommit == repo.getInitialRevision()
            else:
                match = normalizeGitUrl(repoURL) == normalizeGitUrl(url)
            if match:
                if not revision or revision == repo.revision:
                    return repo
                else:
                    candidate = repo
        return candidate

    def findPathInRepos(self, path, importLoader=None):
        """If the given path is part of the working directory of a git repository
        return that repository and a path relative to it"""
        # importloader is unused until pinned revisions are supported
        candidate = None
        for dir in sorted(self.workingDirs.keys()):
            (url, repo) = self.workingDirs[dir]
            filePath = repo.findRepoPath(path)
            if filePath is not None:
                return repo, filePath, repo.revision, False
            #  XXX support bare repo and particular revisions
            #  need to make sure path isn't ignored in repo or compare candidates
            # filePath, revision, bare = repo.findPath(path, importLoader)
            # if filePath is not None:
            #     if not bare:
            #         return repo, filePath, revision, bare
            #     else:  # if it's bare see if we can find a better candidate
            #         candidate = (repo, filePath, revision, bare)
        return candidate or None, None, None, None

    def createWorkingDir(self, gitUrl, revision="HEAD"):
        localRepoPath = self._createPathForGitRepo(gitUrl)
        repo = Repo.createWorkingDir(gitUrl, localRepoPath, revision)
        # add to workingDirs
        self.workingDirs[os.path.abspath(localRepoPath)] = (gitUrl, repo)
        return repo

    def findRepository(self, repoSpec):
        repoUrl = repoSpec.url
        return self.findGitRepo(findGitRepo(repoUrl)[0])

    def findOrClone(self, repo):
        gitUrl = repo.url
        existingRepo = self.findGitRepo(gitUrl)
        if existingRepo:
            return existingRepo

        # if not found:
        localRepoPath = os.path.abspath(
            self._createPathForGitRepo(repo.workingDir or gitUrl)
        )
        newRepo = repo.clone(localRepoPath)
        # add to workingDirs
        self.workingDirs[localRepoPath] = (gitUrl, newRepo)
        return newRepo


_basepath = os.path.abspath(os.path.dirname(__file__))


class LocalConfig(object):
    """
  Represents the local configuration file, which provides the environment that manifests run in, including:
    instances imported from other ensembles, inputs, environment variables, secrets and local configuration.

  It consists of:
  * a list of ensemble manifests with their local configuration
  * the default local and secret instances
"""

    # don't merge the value of the keys of these dicts:
    replaceKeys = [
        "inputs",
        "attributes",
        "schemas",
        "connections",
        "manifest",
        "environment",
    ]

    # XXX add list of projects to config
    # projects:
    #   - path:
    #     default: True
    #     instance: instances/current
    #     spec: spec

    def __init__(self, path=None, parentConfig=None, validate=True):
        defaultConfig = {"apiVersion": "unfurl/v1alpha1", "kind": "Project"}
        self.config = YamlConfig(
            defaultConfig, path, validate, os.path.join(_basepath, "unfurl-schema.json")
        )
        self.manifests = self.config.config.get(
            "manifests", self.config.config.get("instances", [])  # backward compat
        )
        contexts = self.config.expanded.get("contexts", {})
        if parentConfig:
            parentContexts = parentConfig.config.expanded.get("contexts", {})
            contexts = mergeDicts(
                parentContexts, contexts, replaceKeys=self.replaceKeys
            )
        self.contexts = contexts
        self.parentConfig = parentConfig

    def getContext(self, manifestPath, context):
        localContext = self.contexts.get("defaults", {})
        contextName = "defaults"
        for spec in self.manifests:
            if manifestPath == self.adjustPath(spec["file"]):
                # use the context associated with the given manifest
                contextName = spec.get("context", contextName)
                break

        if contextName != "defaults" and contextName in self.contexts:
            localContext = mergeDicts(
                localContext, self.contexts[contextName], replaceKeys=self.replaceKeys
            )

        return mergeDicts(context, localContext, replaceKeys=self.replaceKeys)

    def adjustPath(self, path):
        """
    Makes sure relative paths are relative to the location of this local config
    """
        return os.path.join(self.config.getBaseDir(), path)

    def getDefaultManifestPath(self):
        if len(self.manifests) == 1:
            return self.adjustPath(self.manifests[0]["file"])
        else:
            for spec in self.manifests:
                if spec.get("default"):
                    return self.adjustPath(spec["file"])
        return None

    def createLocalInstance(self, localName, attributes):
        # local or secret
        from .runtime import NodeInstance

        if "default" in attributes:
            if not "default" in attributes.get(".interfaces", {}):
                attributes.setdefault(".interfaces", {})[
                    "default"
                ] = "unfurl.support.DelegateAttributes"
        if "inheritFrom" in attributes:
            if not "inherit" in attributes.get(".interfaces", {}):
                attributes.setdefault(".interfaces", {})[
                    "inherit"
                ] = "unfurl.support.DelegateAttributes"
        instance = NodeInstance(localName, attributes)
        instance._baseDir = self.config.getBaseDir()
        return instance


class LocalEnv(object):
    """
  This class represents the local environment that an ensemble runs in, including
  the local project it is part of and the home project.
  """

    homeProject = None

    def __init__(self, manifestPath=None, homePath=None, parent=None, project=None):
        """
    If manifestPath is None find the first unfurl.yaml or ensemble.yaml
    starting from the current directory.

    If homepath is set it overrides UNFURL_HOME
    (and an empty string disable the home path).
    Otherwise the home path will be set to UNFURL_HOME or the default home location.
    """
        import logging

        logger = logging.getLogger("unfurl")
        self.logger = logger

        if parent:
            self._projects = parent._projects
            self._manifests = parent._manifests
            self.homeConfigPath = parent.homeConfigPath
        else:
            self._projects = {}
            if project:
                self._projects[project.localConfig.config.path] = project
            self._manifests = {}
            self.homeConfigPath = getHomeConfigPath(homePath)
            if self.homeConfigPath and not os.path.exists(self.homeConfigPath):
                logger.warning(
                    'UNFURL_HOME is set but does not exist: "%s"', self.homeConfigPath
                )

        if self.homeConfigPath:
            self.homeProject = self.getProject(self.homeConfigPath, None)

        self.manifestPath = None
        if manifestPath:
            # if manifestPath does not exist check project config
            if not os.path.exists(manifestPath):
                # XXX check if the manifest is named in the project config
                # pathORproject = self.findProject(os.path.dirname(manifestPath))
                # if pathORproject:
                #    self.manifestPath = pathORproject.getInstance(manifestPath)
                # else:
                raise UnfurlError(
                    "Ensemble manifest does not exist: '%s'"
                    % os.path.abspath(manifestPath)
                )
            else:
                pathORproject = self.findManifestPath(manifestPath)
        else:
            # not specified: search current directory and parents for either a manifest or a project
            pathORproject = self.searchForManifestOrProject(".")

        if isinstance(pathORproject, Project):
            self.project = pathORproject
            if not self.manifestPath:
                self.manifestPath = pathORproject.findDefaultInstanceManifest()
        else:
            self.manifestPath = pathORproject
            if project:
                self.project = project
            else:
                self.project = self.findProject(os.path.dirname(pathORproject))

        self.instanceRepo = self._getInstanceRepo()
        self.config = (
            self.project
            and self.project.localConfig
            or self.homeProject
            and self.homeProject.localConfig
            or LocalConfig()
        )

    def getVaultPassword(self, vaultId="default"):
        secret = os.getenv("UNFURL_VAULT_%s_PASSWORD" % vaultId.upper())
        if not secret:
            context = self.getContext()
            secret = (
                context.get("secrets", {})
                .get("attributes", {})
                .get("vault_%s_password" % vaultId)
            )
        return secret

    def getManifest(self, path=None):
        from .yamlmanifest import YamlManifest

        if path and path != self.manifestPath:
            localEnv = LocalEnv(path, self.homeConfigPath, self)
            return localEnv.getManifest()
        else:
            manifest = self._manifests.get(self.manifestPath)
            if not manifest:
                vaultId = "default"
                vault = makeVaultLib(self.getVaultPassword(vaultId), vaultId)
                if vault:
                    self.logger.info(
                        "Vault password found, configuring vault id: %s", vaultId
                    )
                manifest = YamlManifest(localEnv=self, vault=vault)
                self._manifests[self.manifestPath] = manifest
            return manifest

    def getProject(self, path, homeProject):
        path = Project.normalizePath(path)
        project = self._projects.get(path)
        if not project:
            project = Project(path, homeProject)
            self._projects[path] = project
        return project

    # manifestPath specified
    #  doesn't exist: error
    #  is a directory: either instance repo or a project
    def findManifestPath(self, manifestPath):
        if not os.path.exists(manifestPath):
            raise UnfurlError(
                "Manifest file does not exist: '%s'" % os.path.abspath(manifestPath)
            )

        if os.path.isdir(manifestPath):
            test = os.path.join(manifestPath, DefaultNames.Ensemble)
            if os.path.exists(test):
                return test
            else:
                test = os.path.join(manifestPath, DefaultNames.LocalConfig)
                if os.path.exists(test):
                    return self.getProject(test, self.homeProject)
                else:
                    message = (
                        "Can't find an Unfurl ensemble or project in folder '%s'"
                        % manifestPath
                    )
                    raise UnfurlError(message)
        else:
            return manifestPath

    def _getInstanceRepo(self):
        instanceDir = os.path.dirname(self.manifestPath)
        if self.project and instanceDir in self.project.workingDirs:
            return self.project.workingDirs[instanceDir][1]
        else:
            return Repo.findContainingRepo(instanceDir)

    def getRepos(self):
        if self.project:
            repos = self.project.getRepos()
        else:
            repos = []
        if self.instanceRepo and self.instanceRepo not in repos:
            return repos + [self.instanceRepo]
        else:
            return repos

    def searchForManifestOrProject(self, dir):
        current = os.path.abspath(dir)
        while current and current != os.sep:
            test = os.path.join(current, DefaultNames.Ensemble)
            if os.path.exists(test):
                return test

            test = os.path.join(current, DefaultNames.LocalConfig)
            if os.path.exists(test):
                return self.getProject(test, self.homeProject)

            current = os.path.dirname(current)

        message = "Can't find an Unfurl ensemble or project in the current directory (or any of the parent directories)"
        raise UnfurlError(message)

    def findProject(self, testPath):
        """
    Walk parents looking for unfurl.yaml
    """
        path = Project.findPath(testPath)
        if path is not None:
            return self.getProject(path, self.homeProject)
        return None

    def getContext(self, context=None):
        """
        Return a new context that merges the given context with the local context.
        """
        return self.config.getContext(self.manifestPath, context or {})

    def getEngine(self):
        context = self.getContext()
        runtime = context.get("runtime")
        if runtime:
            return runtime
        if self.project and self.project.venv:
            return "venv:" + self.project.venv
        if self.homeProject and self.homeProject.venv:
            return "venv:" + self.homeProject.venv
        return None

    def getLocalInstance(self, name, context):
        assert name in ["locals", "secrets", "local", "secret"]
        local = context.get(name, {})
        return (
            self.config.createLocalInstance(
                name.rstrip("s"), local.get("attributes", {})
            ),
            local,
        )

    def findGitRepo(self, repoURL, isFile=True, revision=None):
        repo = None
        if self.project:
            repo = self.project.findGitRepo(repoURL, revision)
        if not repo:
            if self.homeProject:
                return self.homeProject.findGitRepo(repoURL, revision)
        return repo

    def findOrCreateWorkingDir(
        self, repoURL, isFile=True, revision=None, basepath=None
    ):
        repo = self.findGitRepo(repoURL, revision)
        # git-local repos must already exist
        if not repo and not repoURL.startswith("git-local://"):
            if self.project and (
                basepath is None or self.project.isPathInProject(basepath)
            ):
                project = self.project
            else:
                project = self.homeProject
            if project:
                repo = project.createWorkingDir(repoURL, revision)
        if not repo:
            return None, None, None
        return (repo, repo.revision, revision and repo.revision != revision)

    def findPathInRepos(self, path, importLoader=None):
        """If the given path is part of the working directory of a git repository
        return that repository and a path relative to it"""
        # importloader is unused until pinned revisions are supported
        if self.instanceRepo:
            repo = self.instanceRepo
            filePath = repo.findRepoPath(path)
            if filePath is not None:
                return repo, filePath, repo.revision, False

        candidate = None
        repo = None
        if self.project:
            repo, filePath, revision, bare = self.project.findPathInRepos(
                path, importLoader
            )
            if repo:
                if not bare:
                    return repo, filePath, revision, bare
                else:
                    candidate = (repo, filePath, revision, bare)

        if self.homeProject:
            repo, filePath, revision, bare = self.homeProject.findPathInRepos(
                path, importLoader
            )
        if repo:
            if bare and candidate:
                return candidate
            else:
                return repo, filePath, revision, bare
        return None, None, None, None

    def mapValue(self, val):
        """
        Evaluate using project home as a base dir.
        """
        from .runtime import NodeInstance
        from .eval import mapValue

        instance = NodeInstance()
        instance._baseDir = self.config.config.getBaseDir()
        return mapValue(val, instance)
