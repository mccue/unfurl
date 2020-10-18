#!/usr/bin/env python
"""
Applies a Unfurl ensemble

For each configuration, run it if required, then record the result
"""
from __future__ import print_function

from .yamlmanifest import runJob
from .support import Status
from . import __version__, initLogging, getHomeConfigPath, DefaultNames
from .init import createProject, cloneEnsemble, createHome
from .util import filterEnv
from .localenv import LocalEnv
import click
import sys
import os
import os.path
import traceback
import logging
import functools
import subprocess
import shlex
import json

_latestJobs = []  # for testing


def option_group(*options):
    return lambda func: functools.reduce(lambda a, b: b(a), options, func)


@click.group()
@click.pass_context
@click.option(
    "--home",
    envvar="UNFURL_HOME",
    type=click.Path(exists=False),
    help="Path to .unfurl_home",
)
@click.option("--runtime", envvar="UNFURL_RUNTIME", help="use this runtime")
@click.option(
    "--no-runtime",
    envvar="UNFURL_NORUNTIME",
    default=False,
    is_flag=True,
    help="ignore runtime settings",
)
@click.option("-v", "--verbose", count=True, help="verbose mode (-vvv for more)")
@click.option(
    "-q",
    "--quiet",
    default=False,
    is_flag=True,
    help="Only output errors to the stdout",
)
@click.option(
    "--logfile", default=None, help="Log file for messages during quiet operation"
)
@click.option(
    "--tmp",
    envvar="UNFURL_TMPDIR",
    type=click.Path(exists=True),
    help="Directory for saving temporary files",
)
@click.option("--loglevel", envvar="UNFURL_LOGGING", help="log level (overrides -v)")
def cli(ctx, verbose=0, quiet=False, logfile=None, loglevel=None, tmp=None, **kw):
    # ensure that ctx.obj exists and is a dict (in case `cli()` is called
    # by means other than the `if` block below
    ctx.ensure_object(dict)
    ctx.obj.update(kw)

    if tmp is not None:
        os.environ["UNFURL_TMPDIR"] = tmp

    levels = [logging.INFO, logging.DEBUG, 5, 5, 5]
    if quiet:
        logLevel = logging.CRITICAL
    else:
        # TRACE (5)
        logLevel = levels[min(verbose, 3)]

    if loglevel:  # UNFURL_LOGGING overrides command line
        logLevel = dict(CRITICAL=50, ERROR=40, WARNING=30, INFO=20, DEBUG=10)[
            loglevel.upper()
        ]
    if logLevel == logging.CRITICAL:
        verbose = -1
    elif logLevel > logging.INFO:
        verbose = 0
    else:
        verbose = levels.index(logLevel) + 1
    ctx.obj["verbose"] = verbose
    initLogging(logLevel, logfile)


jobControlOptions = option_group(
    click.option(
        "--dryrun",
        default=False,
        is_flag=True,
        help="Do not modify anything, just do a dry run.",
    ),
    click.option(
        "--commit",
        default=False,
        is_flag=True,
        help="Commit modified files to the instance repository. (Default: False)",
    ),
    click.option(
        "--dirty",
        default=False,
        is_flag=True,
        help="Run even if there are uncommitted changes in the instance repository. (Default: False)",
    ),
    click.option("-m", "--message", help="commit message to use"),
    click.option(
        "--jobexitcode",
        type=click.Choice(["error", "degraded", "never"]),
        default="never",
        help="Set exit code to 1 if job status is not ok.",
    ),
)

commonJobFilterOptions = option_group(
    click.option("--template", help="TOSCA template to target"),
    click.option("--instance", help="instance name to target"),
    click.option("--query", help="Run the given expression upon job completion"),
    click.option("--trace", default=0, help="Set the query's trace level"),
    click.option(
        "--output",
        type=click.Choice(["text", "json", "none"]),
        default="text",
        help="How to print summary of job run",
    ),
)


@cli.command(short_help="Record and run an ad-hoc command")
@click.pass_context
# @click.argument("action", default="*:upgrade")
@click.option("--ensemble", default="", type=click.Path(exists=False))
# XXX:
# @click.option(
#     "--append", default=False, is_flag=True, help="add this command to the previous"
# )
# @click.option(
#     "--replace", default=False, is_flag=True, help="replace the previous command"
# )
@jobControlOptions
@commonJobFilterOptions
@click.option("--host", help="host to run the command on")
@click.option("--operation", help="TOSCA operation to run")
@click.option("--module", help="ansible module to run (default: command)")
@click.argument("cmdline", nargs=-1, type=click.UNPROCESSED)
def run(ctx, instance="root", cmdline=None, **options):
    """
    Run an ad-hoc command in the context of the given ensemble.
    Use "--" to separate the given command line, for example:

    > unfurl run -- echo 'hello!'

    If --host or --module is set, the ansible configurator will be used. e.g.:

    > unfurl run --host=example.com -- echo 'hello!'
    """
    options.update(ctx.obj)
    options["instance"] = instance
    options["cmdline"] = cmdline
    return _run(options.pop("ensemble"), options, ctx.info_name)


def _run(ensemble, options, workflow=None):
    if workflow:
        options["workflow"] = workflow

    localEnv = LocalEnv(ensemble, options.get("home"))
    if not options.get("no_runtime"):
        runtime = options.get("runtime")
        if not runtime:
            runtime = localEnv.getEngine()
        if runtime and runtime != ".":
            return _runRemote(runtime, ensemble, options, localEnv)
    return _runLocal(ensemble, options)


def _venv(runtime, env):
    if env is None:
        env = os.environ.copy()
    # see virtualenv activate
    env.pop("PYTHONHOME", None)  # unset if set
    env["VIRTUAL_ENV"] = runtime
    env["PATH"] = os.path.join(runtime, "bin") + os.pathsep + env.get("PATH", "")
    return env


def _remoteCmd(runtime, cmdLine, localEnv):
    context = localEnv.getContext()
    kind, sep, rest = runtime.partition(":")
    if context.get("environment"):
        addOnly = kind == "docker"
        env = filterEnv(localEnv.mapValue(context["environment"]), addOnly=addOnly)
    else:
        env = None

    if kind == "venv":
        return (
            _venv(runtime, env),
            ["python", "-m", "unfurl", "--no-runtime"] + cmdLine,
            False,
        )
    # elif docker: docker $container -it run $cmdline
    else:
        # treat as shell command
        cmd = shlex.split(runtime)
        return env, cmd + ["--no-runtime"] + cmdLine, True


def _runRemote(runtime, ensemble, options, localEnv):
    import logging

    logger = logging.getLogger("unfurl")
    logger.debug('running command remotely on "%s"', runtime)
    cmdLine = sys.argv[1:]
    env, remote, shell = _remoteCmd(runtime, cmdLine, localEnv)
    rv = subprocess.call(remote, env=env, shell=shell)
    if options.get("standalone_mode") is False:
        return rv
    else:
        sys.exit(rv)


def _runLocal(ensemble, options):
    job = runJob(ensemble, options)
    _latestJobs.append(job)
    if not job:
        click.echo("Unable to create job")
    elif job.unexpectedAbort:
        click.echo("Job unexpected aborted")
        if options.get("verbose", 0) > 0:
            raise job.unexpectedAbort
    else:
        jsonSummary = {}
        summary = options.get("output")
        if summary == "text":
            click.echo(job.summary())
        elif summary == "json":
            jsonSummary = job.jsonSummary()

        query = options.get("query")
        if query:
            result = job.runQuery(query, options.get("trace"))
            if summary == "json":
                jsonSummary["query"] = query
                jsonSummary["result"] = result
            else:
                click.echo("query: " + query)
                click.echo(result)
        if jsonSummary:
            click.echo(json.dumps(jsonSummary))

    if not job or (
        "jobexitcode" in options
        and options["jobexitcode"] != "never"
        and Status[options["jobexitcode"]] <= job.status
    ):
        if options.get("standalone_mode") is False:
            return 1
        else:
            sys.exit(1)
    else:
        return 0


# XXX update help text sans "configurations"
deployFilterOptions = option_group(
    click.option(
        "--add", default=True, is_flag=True, help="run newly added configurations"
    ),
    click.option(
        "--update",
        default=True,
        is_flag=True,
        help="run configurations that whose spec has changed but don't require a major version change",
    ),
    click.option(
        "--repair",
        type=click.Choice(["error", "degraded", "missing", "none"]),
        default="error",
        help="re-run configurations that are in an error or degraded state",
    ),
    click.option(
        "--upgrade",
        default=False,
        is_flag=True,
        help="run configurations with major version changes or whose spec has changed",
    ),
    click.option(
        "--force",
        default=False,
        is_flag=True,
        help="(re)run operation regardless of instance's status or state",
    ),
    click.option(
        "--prune",
        default=False,
        is_flag=True,
        help="delete instances that are no longer used",
    ),
)


@cli.command()
@click.pass_context
@click.argument("ensemble", default="", type=click.Path(exists=False))
@commonJobFilterOptions
@deployFilterOptions
@jobControlOptions
def deploy(ctx, ensemble=None, **options):
    """
    Deploy the given ensemble
    """
    options.update(ctx.obj)
    return _run(ensemble, options, ctx.info_name)


@cli.command(short_help="Check the status of each instance")
@click.pass_context
@click.argument("ensemble", default="", type=click.Path(exists=False))
@commonJobFilterOptions
@jobControlOptions
def check(ctx, ensemble=None, **options):
    """
    Check and update the status of the ensemble's instances
    """
    options.update(ctx.obj)
    return _run(ensemble, options, ctx.info_name)


@cli.command(short_help="run the discover workflow")
@click.pass_context
@click.argument("ensemble", default="", type=click.Path(exists=False))
@commonJobFilterOptions
@jobControlOptions
def discover(ctx, ensemble=None, **options):
    """
    Update configuration by probing live instances associated with the ensemble
    """
    options.update(ctx.obj)
    return _run(ensemble, options, ctx.info_name)


@cli.command()
@click.pass_context
@click.argument("ensemble", default="", type=click.Path(exists=False))
@commonJobFilterOptions
@jobControlOptions
def undeploy(ctx, ensemble=None, **options):
    """
    Destroy what was deployed.
    """
    options.update(ctx.obj)
    return _run(ensemble, options, ctx.info_name)


@cli.command()
@click.pass_context
@click.argument("ensemble", default="", type=click.Path(exists=False))
@commonJobFilterOptions
@jobControlOptions
def stop(ctx, ensemble=None, **options):
    """
    Stop running instances.
    """
    options.update(ctx.obj)
    return _run(ensemble, options, ctx.info_name)


@cli.command(short_help="Print the given deployment plan")
@click.pass_context
@click.argument("ensemble", default="", type=click.Path(exists=False))
@commonJobFilterOptions
@deployFilterOptions
@click.option("--workflow", default="deploy", help="plan workflow (default: deploy)")
def plan(ctx, ensemble=None, **options):
    "Print the given deployment plan"
    options.update(ctx.obj)
    options["planOnly"] = True
    # XXX show status and task to run including preview of generated templates, cmds to run etc.
    return _run(ensemble, options)


@cli.command(short_help="Create a new unfurl project")
@click.pass_context
@click.argument("projectdir", default=".", type=click.Path(exists=False))
@click.option(
    "--mono", default=False, is_flag=True, help="Create one repository for the project."
)
@click.option(
    "--existing",
    default=False,
    is_flag=True,
    help="Add project to nearest existing repository.",
)
def init(ctx, projectdir, **options):
    """
unfurl init [project] # creates a unfurl project with new spec and instance repos
"""
    options.update(ctx.obj)

    if os.path.exists(projectdir):
        if not os.path.isdir(projectdir):
            raise click.ClickException(
                'Can not create project in "' + projectdir + '": file already exists'
            )
        elif os.listdir(projectdir):
            raise click.ClickException(
                'Can not create project in "' + projectdir + '": folder is not empty'
            )

    homePath, projectPath = createProject(projectdir, **options)
    if homePath:
        click.echo("unfurl home created at %s" % homePath)
    click.echo("New Unfurl project created at %s" % projectPath)


# XXX add --upgrade option
@cli.command(short_help="Manage the unfurl home project")
@click.pass_context
@click.option(
    "--render",
    default=False,
    is_flag=True,
    help="Generate files only (don't create repository)",
)
@click.option("--init", default=False, is_flag=True, help="Create a new home project")
@click.option(
    "--replace",
    default=False,
    is_flag=True,
    help="Replace (and backup) current home project",
)
def home(ctx, init=False, render=False, replace=False, **options):
    options.update(ctx.obj)
    if not render and not init:
        # just display the current home location
        click.echo(getHomeConfigPath(options.get("home")))
        return

    homePath = createHome(render=render, replace=replace, **options)
    action = "rendered" if render else "created"
    if homePath:
        click.echo("unfurl home %s at %s" % (action, homePath))
    else:
        currentHome = getHomeConfigPath(options.get("home"))
        if currentHome:
            click.echo("Can't %s home, it already exists at %s" % (action, currentHome))
        else:
            click.echo("Error: home path is empty")


@cli.command(short_help="Clone a project, ensemble or service template")
@click.pass_context
@click.argument(
    "source",
    # help="path to a service template or ensemble",
)
@click.argument(
    "dest",
    type=click.Path(exists=False),
    default=".",  # , help="path to the new ensemble"
)
@click.option(
    "--mono", default=False, is_flag=True, help="Create one repository for the project."
)
@click.option(
    "--existing",
    default=False,
    is_flag=True,
    help="Add project to nearest existing repository.",
)
def clone(ctx, source, dest, **options):
    """Create a new ensemble or project from a service template or an existing ensemble or project."""
    options.update(ctx.obj)

    message = cloneEnsemble(source, dest, **options)
    click.echo(message)


@cli.command(
    short_help="Run a git command across all repositories",
    context_settings={"ignore_unknown_options": True},
)
@click.pass_context
@click.option(
    "--dir", default=".", type=click.Path(exists=True), help="path to spec repository"
)
@click.argument("gitargs", nargs=-1)
def git(ctx, gitargs, dir="."):
    """
unfurl git --dir=/path/to/start [gitoptions] [gitcmd] [gitcmdoptions]: Runs command on each project repository.
"""
    localEnv = LocalEnv(dir, ctx.obj.get("home"))
    repos = localEnv.getRepos()
    status = 0
    if not repos:
        click.echo("Can't run git command, no repositories found")
    for repo in repos:
        repoPath = os.path.relpath(repo.workingDir, os.path.abspath(dir))
        click.echo("*** Running 'git %s' in './%s'" % (" ".join(gitargs), repoPath))
        _status, stdout, stderr = repo.runCmd(gitargs)
        click.echo(stdout + "\n")
        if stderr.strip():
            click.echo(stderr + "\n", color="red")
        if _status != 0:
            status = _status

    return status


@cli.command()
def version():
    "Print the current version"
    click.echo("unfurl version %s" % __version__)


@cli.command()
@click.pass_context
@click.argument("cmd", nargs=1, default="")
def help(ctx, cmd=""):
    "Get help on a command"
    if not cmd:
        click.echo(cli.get_help(ctx.parent), color=ctx.color)
        return

    command = ctx.parent.command.commands.get(cmd)
    if command:
        ctx.info_name = cmd  # hack
        click.echo(command.get_help(ctx), color=ctx.color)
    else:
        raise click.UsageError("no help found for unknown command '%s'" % cmd, ctx=ctx)


def main():
    obj = {"standalone_mode": False}
    try:
        rv = cli(standalone_mode=False, obj=obj)
        sys.exit(rv or 0)
    except click.Abort:
        click.secho("Aborted!", fg="red", err=True)
        sys.exit(1)
    except click.ClickException as e:
        if obj.get("verbose", 0) > 0:
            traceback.print_exc(file=sys.stderr)
        click.secho("Error: %s" % e.format_message(), fg="red", err=True)
        sys.exit(e.exit_code)
    except Exception as err:
        if obj.get("verbose", 0) > 0:
            traceback.print_exc(file=sys.stderr)
        else:
            click.secho("Exiting with error: " + str(err), fg="red", err=True)
        sys.exit(1)


def vaultclient():
    try:
        localEnv = LocalEnv(".")
    except Exception as err:
        click.echo(str(err), err=True)
        return 1

    print(localEnv.getVaultPassword() or "")
    return 0


if __name__ == "__main__":
    main()
