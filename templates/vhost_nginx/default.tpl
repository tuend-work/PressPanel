server {
    listen 80;
    server_name {{DOMAIN}} www.{{DOMAIN}};
    root {{DOCUMENT_ROOT}};
    index index.php index.html;

    access_log /home/{{USERNAME}}/logs/{{DOMAIN}}.access.log;
    error_log /home/{{USERNAME}}/logs/{{DOMAIN}}.error.log;

    location / {
        try_files $uri $uri/ /index.php?$args;
    }

    location ~ \.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:{{PHP_SOCK}};
    }
}
