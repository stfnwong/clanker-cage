import click

@click.group()
def cli():
    pass

@cli.command()
@click.option('--project', '-p', default='.', help='Project root')
@click.option('--stop-proxy', is_flag=True, help='Stop proxy after session')
@click.option('--no-proxy', is_flag=True, help='Run offline')
def run(project, stop_proxy, no_proxy):
    """Start a new clanker session."""
    # conductor logic

@cli.command()
@click.argument('session_id')
def log(session_id):
    """View a session transcript."""
    # render session

@cli.command()
@click.argument('session_id')
def resume(session_id):
    """Resume an existing session."""
    # load history, start container with it

@cli.command()
def stop():
    """Stop the provider proxy service."""
    # proxy.stop()



def get_initial_prompt(args):
    if args.pipe:
        return sys.stdin.read()
    if args.prompt:
        return args.prompt
    if args.editor:
        return open_editor_and_get_text()
    return None  # no initial prompt, just drop into shell


if __name__ == '__main__':
    cli()
