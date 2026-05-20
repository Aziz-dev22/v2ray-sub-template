#!/bin/bash

clear

echo "======================================="
echo " V2Ray Premium Subscription Installer "
echo "======================================="

read -p "Enter your domain: " DOMAIN

apt update -y
apt install nginx curl certbot python3-certbot-nginx -y

mkdir -p /var/www/sub

cat > /var/www/sub/index.html << 'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Premium Subscription</title>

<style>

body{
background:#070b14;
font-family:sans-serif;
color:white;
margin:0;
padding:40px;
}

.title{
font-size:42px;
color:#9f5cff;
margin-bottom:30px;
}

.grid{
display:grid;
grid-template-columns:repeat(auto-fit,minmax(300px,1fr));
gap:20px;
}

.card{
background:rgba(255,255,255,0.05);
padding:25px;
border-radius:24px;
backdrop-filter:blur(10px);
border:1px solid rgba(255,255,255,0.08);
transition:0.3s;
}

.card:hover{
transform:translateY(-5px);
border-color:#9f5cff;
}

.ping{
color:#3dff7a;
margin-top:10px;
}

.badge{
display:inline-block;
background:#6f3dff;
padding:5px 10px;
border-radius:10px;
margin-top:10px;
}

</style>

</head>

<body>

<div class="title">
🚀 Premium Subscription
</div>

<div class="grid">

<div class="card">
<h2>🇩🇪 Germany Reality</h2>
<div class="ping">32 ms</div>
<div class="badge">REALITY</div>
</div>

<div class="card">
<h2>🇫🇷 Paris Vision</h2>
<div class="ping">45 ms</div>
<div class="badge">VISION</div>
</div>

<div class="card">
<h2>🇳🇱 NL Fast</h2>
<div class="ping">58 ms</div>
<div class="badge">FAST</div>
</div>

</div>

</body>
</html>
EOF

cat > /etc/nginx/sites-available/sub << EOF
server {
    listen 80;
    server_name $DOMAIN;

    root /var/www/sub;
    index index.html;

    location / {
        try_files \$uri \$uri/ =404;
    }
}
EOF

ln -sf /etc/nginx/sites-available/sub /etc/nginx/sites-enabled/sub

rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl restart nginx

certbot --nginx -d $DOMAIN --non-interactive --agree-tos -m admin@$DOMAIN --redirect

echo ""
echo "======================================="
echo " Installation Completed Successfully "
echo "======================================="
echo ""
echo " https://$DOMAIN "
