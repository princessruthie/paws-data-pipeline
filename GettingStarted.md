# Getting Started

## All of our code is in containers
- Install Docker - `https://docs.docker.com/install`  
- Install Docker Compose - `https://docs.docker.com/compose/install/`      

## Running everything (server and client)
- navigate to src directory `cd .../PAWS-DATA-PIPELINE/src`
- build the machines with `docker compose build`
- start the machines with `docker compose up`
- Note: if you're new to the project, please come to one of the weekly meetings to find out how to get the secrets file. Your servers won't start without this file.

## Running the client (front-end) locally
- navigate to src directory `cd .../PAWS-DATA-PIPELINE/src`
- docker compose `docker compose run server`
- start the frontend with the proxy`npm run start:local`

## Running just server (back-end) locally
- navigate to src directory `cd .../PAWS-DATA-PIPELINE/src`
- set the PAWS_API_HOST=localhost or your ip in `docker compose.yml`
- docker compose `docker compose up` and stop the server
