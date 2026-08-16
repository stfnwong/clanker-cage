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

if __name__ == '__main__':
    cli()
