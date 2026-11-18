# Made by the app at every start (init-mqtt). Only for the broker's login check of Home Assistant users.
daemon off;
user root;
pid /run/nginx.pid;
worker_processes 1;
error_log /proc/1/fd/1 error;

events {
    worker_connections 64;
}

http {
    access_log off;
    gzip off;
    keepalive_timeout 65;
    server_tokens off;
    tcp_nodelay on;
    tcp_nopush on;

    server {
        listen 127.0.0.1:80 default_server;
        server_name _;
        keepalive_timeout 5;
        root /dev/null;

        location /authentication {
            proxy_set_header X-Supervisor-Token "{{ env "SUPERVISOR_TOKEN" }}";
            proxy_pass http://supervisor/auth;
        }

        location = /superuser {
            return 200;
        }

        location = /acl {
            return 200;
        }
    }
}
