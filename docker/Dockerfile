FROM jookies/jasmin

RUN apt-get update && apt-get install git -y \
&& apt-get clean \
&& rm -rf /var/lib/apt/lists/*

RUN pip install git+https://github.com/muriloventuroso/jasmin.git -y

CMD ["jasmind.py", "--enable-interceptor-client", "--enable-dlr-thrower", "--enable-dlr-lookup", "-u", "jcliadmin", "-p", "jclipwd", "-c", "/etc/jasmin/jasmin.cfg"]
