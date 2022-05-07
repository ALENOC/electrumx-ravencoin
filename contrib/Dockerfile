# example of Dockerfile that builds release of electrumx-ravencoin-1.10.0
# ENV variables can be overrided on the `docker run` command

FROM ubuntu:18.04

WORKDIR /
ADD https://github.com/Electrum-RVN-SIG/electrumx-ravencoin/archive/v1.10.3.tar.gz /
RUN tar zxvf *.tar.gz

RUN apt-get update && \
        apt-get -y install cmake gcc libffi-dev build-essential python3.8 python3.8-dev python3-pip libleveldb-dev libssl-dev

RUN python3.8 -m pip install --upgrade pip

# Needed for MacOs
RUN python3.8 -m pip install scikit-build

RUN cd /electrumx* && python3.8 -m pip install -r requirements.txt && python3.8 setup.py install

ENV SERVICES="ssl://:50002,tcp://:50001,rpc://:8000"
ENV COIN=Ravencoin
ENV DB_DIRECTORY=/db
ENV DAEMON_URL="http://username:password@hostname:port/"
ENV ALLOW_ROOT=true
ENV MAX_SEND=10000000
ENV BANDWIDTH_UNIT_COST=50000
ENV CACHE_MB=1000
ENV EVENT_LOOP_POLICY=uvloop

ENV SSL_CERTFILE=/db/ssl_cert/server.crt
ENV SSL_KEYFILE=/db/ssl_cert/server.key

# REQUIRED to connect with the larger electrumx ravencoin server network.
# Full service://(IP/URL):PORT is REQUIRED
# Example: 
# env REPORT_SERVICES = ssl://rvn4lyfe.com:50002,tcp://rvn4lyfe.com:50001

EXPOSE 8000
EXPOSE 50001
EXPOSE 50002

VOLUME /db

RUN mkdir -p "$DB_DIRECTORY" && ulimit -n 1048576

#RUN grep -v "\$ENV::HOME/.rnd" /etc/ssl/openssl.cnf > tmpfile && mv tmpfile /etc/ssl/openssl.cnf

CMD if [ -d "$DB_DIRECTORY/ssl_cert/" ]; then \
        echo "SSL certs exist."; \
    else \ 
        mkdir "$DB_DIRECTORY/ssl_cert" && \
        cd "$DB_DIRECTORY/ssl_cert" && \
        openssl genrsa -out server.key 2048 && \
        openssl req -new -key server.key -out server.csr -subj "/C=AU" && \
        openssl x509 -req -days 1825 -in server.csr -signkey server.key -out server.crt; \
    fi && /usr/bin/python3.8 /usr/local/bin/electrumx_server

#CMD ["/usr/bin/python3.8", "/usr/local/bin/electrumx_server"]

# build it with eg.: `docker build -t electrumx .`
# run it with eg.:
# `docker run -d --net=host -v /home/electrumx/db/:/db -e DAEMON_URL="http://youruser:yourpass@localhost:8766" -e REPORT_SERVICES=tcp://example.com:50001 electrumx`
# for a proper clean shutdown, send TERM signal to the running container eg.: `docker kill --signal="TERM" CONTAINER_ID`
