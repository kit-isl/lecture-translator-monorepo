# Main Components
The overall main components of the pipeline are
* [Browser-frontend](./frontend.md)
* [HTTP API](./lt-api.md)
* [streaming workers](./streaming-workers.md)
* [mediator](./mediator.md)
* [database](./redis.md)

Here's a rough overview of how these components interact:
![](./figures/lt_system_overview.pdf)

# Client-side components
In addition to the server-side components, multiple clients for users to interact with the pipeline exist.

Currently available clients are
* Browser frontend
* Python "audioclient"
* minimal Bash client
