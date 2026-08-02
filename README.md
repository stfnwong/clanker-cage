# CLANKER CAGE

Container image to hold an agent. 

This repo is just a docker image that in which I want to run agents. The reason I want this is to be able to control agent access to other things on my laptop. For now this is specialised around `Claude` because thats what I have a subscription for but the idea would be to generalise this somewhat as time goes by.

TODO: I would actually like to put some sort of harness together for this


## Usage 
The idea is that the container is a kind of throwaway sandbox in which we can constrain the agent. The original motivation is my paranoia about data loss, so at the time of writing there isn't any specific defense against prompt injection, data exfiltration, etc. The imagined workflow looks like 

- Clone this repo 
- From here build the image (`make build`)
- Take `clanker` and copy it somewhere so that its on `$PATH`. 
- The idea of `clanker` is that it gives a set of commands which put a new `clanker-cage` into the current project directory. So `cd to/where/project/is && clanker up && clanker attach` is the intended workflow.
