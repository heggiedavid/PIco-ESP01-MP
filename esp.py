from machine import UART, Pin
import time

class ESP:   
    STATUS_APCONNECTED = 2
    STATUS_SOCKETOPEN = 3
    STATUS_SOCKETCLOSED = 4
    STATUS_NOTCONNECTED = 5
    
    MODE_STATION = 1
    MODE_SOFTAP = 2
    MODE_SOFTAPSTATION = 3
    
    def __init__(self,
                 uart_id=0,
                 tx_pin=0,
                 rx_pin=1,
                 baud_rate=115200,
                 tx_buffer=1024,
                 rx_buffer=2048,
                 debug=False
                 ):
        """ initialise the UART for the ESP8266 module
            Doesn't do anything else
            
            Default to UART 0, tx pin 0, rx pin 1
            Valid pins for UARTS are: UART0: tx=0/12/16, rx=1/13/17, UART1: tx=4/8, rx=5/9
        """
        self._debug = debug
        
        try:
            self._uart = UART(uart_id,
                              baudrate=baud_rate,
                              tx=Pin(tx_pin),
                              rx=Pin(rx_pin),
                              txbuf=tx_buffer,
                              rxbuf=rx_buffer
                              )
        except:
            self._uart = None
            
    def ping(self, host):
        """ Ping the IP or hostname given, returns ms time or None on failure
        """
        reply = self.send_at_cmd('AT+PING="%s"' % host.strip('"'), timeout=5)
        for line in reply.split(b"\r\n"):
            if line and line.startswith(b"+"):
                try:
                    if line[1:5] == b"PING":
                        return int(line[6:])
                    return int(line[1:])
                except ValueError:
                    return None
        raise RuntimeError("Couldn't ping")

    def send_at_cmd(self, at_cmd, timeout=20, retries=3):
        """ Send an AT command, check that we got an OK response,
            and then return the text of the reply.
        """
        for _ in range(retries):
            if self._debug:
                print("tx ---> ", at_cmd)
            
            self._uart.write(bytes(at_cmd, "utf-8"))
            self._uart.write(b"\x0d\x0a")
            stamp = time.time()
            response = b""
            
            while (time.time() - stamp) < timeout:
                if self._uart.any():
                    response += self._uart.read(1)
                    if response[-4:] == b"OK\r\n":
                        break
                    if response[-7:] == b"ERROR\r\n":
                        break
                    if "AT+CWJAP=" in at_cmd:
                        if b"WIFI GOT IP\r\n" in response:
                            break
                    else:
                        if b"WIFI CONNECTED\r\n" in response:
                            break
                    if b"ERR CODE:" in response:
                        break
            
            if self._debug:
                print("<--- rx ", response)
     
            if "AT+CWJAP=" in at_cmd and b"WIFI GOT IP\r\n" in response:
                return response

            if "AT+PING" in at_cmd and b"ERROR\r\n" in response:
                return response
            
            if response[-4:] != b"OK\r\n":
                time.sleep(1)
                continue
            
            return response[:-4]
        
        raise Exception("No OK response to " + at_cmd)
    
    def connect(self, secrets):
        """ Repeatedly try to connect to an access point with the details in
            the passed in 'secrets' dictionary.
        """
        retries = 3
        while True:
            try:
                AP = self.remote_AP
                if AP[0] != secrets["ssid"]:
                    self.join_ap(secrets["ssid"], secrets["password"])
                return True
            except (RuntimeError) as exp:
                print("Failed to connect, retrying\n", exp)
                retries -= 1
                continue
  
    def soft_reset(self):
        """ soft_reset: perform a soft reset of the ESP8266
        """
        if self._uart == None:
            return False

        reply = self.send_at_cmd("AT+RST", timeout=1)
        if reply.strip(b"\r\n") == b"AT+RST":
            time.sleep(2)
            return True

        return False
    
    @property
    def is_connected(self):
        """ Initialize module if not done yet, and check if we're connected to
            an access point, returns True or False
        """
        state = self.status
        if state in (
            self.STATUS_APCONNECTED,
            self.STATUS_SOCKETOPEN,
            self.STATUS_SOCKETCLOSED,
        ):
            return True
        
        return False
    
    @property
    def status(self):
        """The IP connection status number (see AT+CIPSTATUS datasheet for meaning)"""
        replies = self.send_at_cmd("AT+CIPSTATUS", timeout=5).split(b"\r\n")
        for reply in replies:
            if reply.startswith(b"STATUS:"):
                return int(reply[7:8])
        return None
    
    @property
    def remote_AP(self):
        """The name of the access point we're connected to, as a string"""
        stat = self.status

        if stat != self.STATUS_APCONNECTED:
            return [None] * 4
        
        replies = self.send_at_cmd("AT+CWJAP?", timeout=10).split(b"\r\n")

        for reply in replies:
            if not reply.startswith("+CWJAP:"):
                continue
            reply = reply[7:].split(b",")
            for i, val in enumerate(reply):
                reply[i] = str(val, "utf-8")
                try:
                    reply[i] = int(reply[i])
                except ValueError:
                    reply[i] = reply[i].strip('"')  # its a string!
            
            return reply
        
        return [None] * 4
    
    @property
    def mode(self):
        replies = self.send_at_cmd("AT+CWMODE?", timeout=5).split(b"\r\n")
        for reply in replies:
            if reply.startswith(b"+CWMODE:"):
                return int(reply[8:])
        raise RuntimeError("Bad response to CWMODE?")
    
    @mode.setter
    def mode(self, mode):
        """Station or AP mode selection, can be MODE_STATION, MODE_SOFTAP or MODE_SOFTAPSTATION"""
        if not self._initialized:
            self.begin()
        if not mode in (1, 2, 3):
            raise RuntimeError("Invalid Mode")
        self.at_response("AT+CWMODE=%d" % mode, timeout=3)
    
    @property
    def local_ip(self):
        """Our local IP address as a dotted-quad string"""
        reply = self.send_at_cmd("AT+CIFSR").strip(b"\r\n")
        for line in reply.split(b"\r\n"):
            if line and line.startswith(b'+CIFSR:STAIP,"'):
                return str(line[14:-1], "utf-8")
        raise RuntimeError("Couldn't find IP address")
    
    def join_ap(self, ssid, password):  # pylint: disable=invalid-name
        """Try to join an access point by name and password, will return
        immediately if we're already connected and won't try to reconnect"""
        # First make sure we're in 'station' mode so we can connect to AP's
        if self.mode != self.MODE_STATION:
            self.mode = self.MODE_STATION

        router = self.remote_AP
        if router and router[0] == ssid:
            return  # we're already connected!
        for _ in range(3):
            reply = self.send_at_cmd(
                'AT+CWJAP="' + ssid + '","' + password + '"', timeout=15, retries=3
            )

            if b"WIFI CONNECTED" not in reply:
                print("no CONNECTED")
                #raise RuntimeError("Couldn't connect to WiFi")
            if b"WIFI GOT IP" not in reply:
                print("no IP")
                #raise RuntimeError("Didn't get IP address")
            return
    
    def get_APs(self, retries=3):
        """Ask the module to scan for access points and return a list of lists
        with name, RSSI, MAC addresses, etc
        """
        for _ in range(retries):
            try:
                if self.mode != self.MODE_STATION:
                    self.mode = self.MODE_STATION
                scan = self.send_at_cmd("AT+CWLAP", timeout=5).split(b"\r\n")
            except RuntimeError:
                continue
            routers = []
            
            for line in scan:
                if line.startswith(b"+CWLAP:("):
                    router = line[8:-1].split(b",")
                    for i, val in enumerate(router):
                        router[i] = str(val, "utf-8")
                        try:
                            router[i] = int(router[i])
                        except ValueError:
                            router[i] = router[i].strip('"')  # its a string!
                    routers.append(router)
            return routers
