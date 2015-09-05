#
# Copyright (c) 2009 by Matt Warren
# 
# This file is part of qPy.
# 
# qPy is free software; you can redistribute it and/or modify it under
# the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
# 
# qPy is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
# or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU Lesser General
# Public License for more details.
# 
# You should have received a copy of the GNU Lesser General
# Public License along with qPy; if not, write to the Free
# Software Foundation, Inc., 59 Temple Place, Suite 330, Boston,
# MA  02111-1307  USA
# 

#
# Written by Matt Warren
#

#
# 2015.09.04 - edited by Etay Schnapp 
# Added q v3.1 support - timestamp, timespan, compression and UUID support
#

import sys
import socket
import array
import struct
import time
import datetime
import logging
from uuid import UUID
SYNC=True
ASYNC=False
nt = [ 0, 1, 16, 0, 1, 2, 4, 8, 4, 8, 1, 0, 8, 4, 4, 8, 8, 4, 4, 4 ]  #byte length of different datatypes

class timestamp(datetime.datetime):
    def __str__(self):
        return super().__str__().replace("-",".").replace(" ","D")
class Month:
    def __init__(self, x):
        self.i = x
    def __str__(self):
        m = self.i + 24000
        y = m / 12
        return '%(decade)02d%(year)02d-%(month)02d' % {'decade': y/100, 'year': y % 100, 'month':(m+1)%12}
    def __eq__(self, obj):
        if isinstance(obj, Month) : return obj.i == self.i
        return False
class Minute:
    def __init__(self, x):
        self.i = x
    def __str__(self):
        return '%(hour)02d:%(minute)02d' % {'hour': self.i/60, 'minute': self.i % 60}
    def __eq__(self, obj):
        if isinstance(obj, Minute) : return obj.i == self.i
        return False
class Second:
    def __init__(self, x):
        self.i = x
    def __str__(self):
        return '%(minute)s:%(second)02d' % {'minute': str(Minute(self.i/60)), 'second': self.i % 60}
    def __eq__(self, obj):
        if isinstance(obj, Second) : return obj.i == self.i
        return False
class Dict:
    """Dict is a generalized dict.  It just contains the keys and values as two objects and provides a way to 
    interact with it."""
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.length = len(x)

    def __len__(self):
        return self.length 
    
    def __iter__(self):
        self.index = 0
        return self
    
    def next(self):
        if self.index > self.length-1:
            raise StopIteration
        k,v = self.x[self.index], self.y[self.index]
        self.index += 1
        return k,v
    def __str__(self):
        string = ""
        for k,v in self:
            string += '[' +','.join(str(item) for item in k) + ']' + ','.join((str(item) for item in v)) + "\n"
        self.index = 0
        return string
    def __eq__(self, obj):
        if isinstance(obj, Dict) : return self.y == obj.y and self.x == obj.x
        return False
    
class Flip:
    """Flip is a different way to look at table data held in a Dict
    It assumes that the dictionary contains values which are equal length arrays"""
    def __init__(self, d):
        self.x = []  #column names
        self.y = []  #column data (stored by column)
        for k,v in d:
            self.x.append(k)
            self.y.append(v)
        self.length = len(self.y[0])
        self.index = 0
    def __len__(self):
        return self.length 
    def __iter__(self):
        self.index = 0
        return self
    def next(self):
        """Return the row"""
        if self.index > self.length-1:
            raise StopIteration
        row = []
        for v in self.y:
            row.append(v[self.index])
        self.index += 1
        return row
    def __str__(self):
        string = ""
        for row in self:
            string += ','.join((str(item) for item in row)) + "\n"
        self.index = 0
        return string
    def __eq__(self, obj):
        if isinstance(obj, Flip) : return self.y == obj.y and self.x == obj.x
        return False
    def __getitem__(self, index):
        row = []
        for v in self.y:
            row.append(v[index])
        return row
    
def td(x):
    """A Dict containing two Flips is how keyed tables are encoded, td joins the 2 Dict objects into a single Flip object"""
    if isinstance(x, Flip): return x
    if not isinstance(x, Dict): raise Exception('This function takes a Dict type')
    a = x.x
    b = x.y
    x = []
    for item in a.x: x.append(item)
    for item in b.x: x.append(item)
    y = []
    for item in a.y: y.append(item)
    for item in b.y: y.append(item)
    return Flip(Dict(x,y))
          
# 86400000 is number of milliseconds in a day
# 10957 is days offset between UNIX Epoch and kdb Epoch
k = 86400000L * 10957
STDOFFSET = -time.timezone

      
class q:
    
    RECONNECT_ATTEMPTS = 5  # Number of reconnect attempts to make before throwing exception
    RECONNECT_WAIT = 5000 # Milliseconds to wait between reconnect attempts 
    MAX_MSG_QUERY_LENGTH = 1024 # Maximum number of characters from query to return in exception message
    MAX_MSG_LIST_LENGTH = 100 # Maximum length of a data list specified in a query before it is summarized in exception message

    def lg(self, x):
        """local time to UTC offset"""
        return x + STDOFFSET
    
    def gl(self, x):
        """UTC to local time offset"""
        return x - STDOFFSET

    def __init__(self, host, port, user):
        self.host=host
        self.port=port
        self.user=user
        self.remote_ver = 0
        self.compress = False
        self.localhost = False
        self.offset = 0
        self.sock=socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.connect()
        
    def close(self):
        self.sock.close()
        
    def connect(self, attempts=1):
        if self.host=='' :
            raise Exception('bad host')
        for attempt in range(attempts):
            try:
                self.sock.connect((self.host,self.port))
                
                # check if local address
                if(((self.sock.getsockname()[0]) == (self.sock.getpeername()[0])) or 
                   ((self.sock.getsockname()[0]) == '127.0.0.1' ) or
                   ((self.sock.getsockname()[0]) == 'localhost' )):
                    self.localhost = True
                
                # check and turn on TCP Keepalive
                x = self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE)
                if (x == 0):
                    x = self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                
                login = array.array('B')  #signed char array (bytes)
                login.fromstring(self.user + "\3")
                login.append(0) #null terminated string
                self.sock.send(login.tostring())
                result = self.sock.recv(1)  #blocking recv
                if not result:
                    login = array.array('B')  #signed char array (bytes)
                    login.fromstring(self.user)
                    login.append(0) #null terminated string
                    self.sock.send(login.tostring())
                    result = self.sock.recv(1)  #blocking recv
                    if not result:
                        raise Exception("access denied")
                
                self.remote_ver = ord(result[0])
                
                
            except Exception as e:
                raise Exception ('unable to connect to host: ' + str(type(e)) + ':' + e.message)
        
    def ns(self, str):
        if str=='' or str==None:
            return 0
        else:
            return str.find('\000')
        
    def n(self, x):
        if isinstance(x, Dict):
            return self.n(x.x)
        elif isinstance(x, array.array) or isinstance(x, list):
            return len(x)
        else:
            return 1
    
    def _nx(self, x):
        qtype = self._qtype(x)
        if qtype == 99:
            return 1 + self._nx(x.x) + self._nx(x.y)
        if qtype == 98:
            return 3 + self._nx(x.x) + self._nx(x.y)
        if qtype < 0:
            if qtype == -11:
                return 2+len(x)
            else:
                return 1 + nt[-qtype]
        j = 6
        n = self.n(x)
        if qtype == 0 or qtype == 11:
            for i in range(0, n):
                if qtype == 0:
                    j += self._nx(x[i])
                else:
                    1 + len(x[i])
        else :
            j += n * nt[qtype];
        return j;
    
    def _qtype(self, x):
        """Encode the type of x as an integer that is interpreted by q"""
        #TODO figure out how to deal with array types
        if isinstance(x, list):return 0

        if isinstance(x, array.array):
            if x.typecode == 'c':
                return 10
            elif x.typecode == 'h':
                return 10
            elif x.typecode == 'i':
                return 6
            elif x.typecode == 'l':
                return 7
            elif x.typecode == 'f':
                return 8
            elif x.typecode == 'd':
                return 8
            else:
                return 0
                                    
        if isinstance(x,bool):
            return -1
        elif isinstance(x,UUID):
            return -2
        elif isinstance(x,int):
            return -6
        elif isinstance(x,float):
            return -8
        elif isinstance(x,long):
            return -7
        elif isinstance(x,str):
            return -11
        elif isinstance(x,timestamp):
            return -12
        elif isinstance(x,Month):
            return -13
        elif isinstance(x,datetime.datetime):
            return -15
        elif isinstance(x,datetime.date):
            return -14
        elif isinstance(x,datetime.timedelta):
            return -16
        elif isinstance(x,Minute):
            return -17
        elif isinstance(x,Second):
            return -18
        elif isinstance(x,datetime.time):
            return -19
        elif isinstance(x,Flip):
            return 98
        elif isinstance(x,Dict):
            return 99
        else:
            return 0
    
    def _wb(self, x, message):
        message.fromstring(struct.pack('b', x))
        
    def _wg(self, x, message):
        if( self.remote_ver < 3 ):
            raise Exception("KDB 3.0 needed for UUID support")
        message.fromstring(x.bytes)
    
    def _wc(self, x, message):
        message.fromstring(struct.pack('c', x))
    
    def _wi(self, x, message):
        message.fromstring(struct.pack('>i', x))
    
    def _wd(self, x, message):
        message.fromstring(struct.pack('>i', x.toordinal() - datetime.date(2000, 1, 1).toordinal()))
        
    def _wdt(self, x, message):
        message.fromstring(struct.pack('>d', (self.lg( time.mktime(x.timetuple())+(x.microsecond/1000000.) )*1000. -k) / 8.64e7 ))

    def _wp(self, x, message):
        if( self.remote_ver < 3 ):
            raise Exception("KDB 3.0 needed for Timestamp support")    
        d = (x - datetime.datetime(2000,1,1) )
        val = (long((d.days * 24 * 3600) + (d.seconds)) * 1000000) + d.microseconds
        message.fromstring(struct.pack('>q', val*1000))
    
    def _wn(self, x, message):
        if( self.remote_ver < 3 ):
            raise Exception("KDB 3.0 needed for Timespan support")    
        val = (long((x.days * 24 * 3600) + (x.seconds)) * 1000000) + x.microseconds
        message.fromstring(struct.pack('>q', val* 1000))
    
    def _wt(self, x, message):
        message.fromstring(struct.pack('>i', ( x.hour*3600 + x.minute*60 + x.second + (x.microsecond+100)/1000000. )*1000. ))
    
    def _we(self, x, message):
        message.fromstring(struct.pack('>f', x))
    
    def _wj(self, x, message):
        message.fromstring(struct.pack('>q', x))
    
    def _wf(self, x, message):
        message.fromstring(struct.pack('>d', x))
    
    def _wh(self, x, message):
        message.fromstring(struct.pack('>h', x))
    
    def _ws(self, x, message):
        message.fromstring(x)
        message.fromstring(struct.pack('b',0))
    
    def _wdict(self, x, message):
        self._write(x.x, message)
        self._write(x.y, message)
        
    def _wmms(self, x, message):
        message.fromstring(struct.pack('>i', x.i))
        
    def _write(self, x, message):
        """determine the type of x and write it to the binary message for output"""
        t = self._qtype(x)
        message.fromstring(struct.pack('b', t))
        writeType = {
            -1: self._wb,
            -2: self._wg,            
            -4: self._wb,
            -5: self._wh,
            -6: self._wi,
            -7: self._wj,
            -8: self._we,
            -9: self._wf,
            -10: self._wc,            
            -11: self._ws,
            -12: self._wp,
            -13: self._wmms,
            -14: self._wd,
            -15: self._wdt,
            -16: self._wn,            
            -17: self._wmms,
            -18: self._wmms,
            -19: self._wt,
            0: self._write,
            1: self._wb,
            2: self._wg,             
            4: self._wb,
            5: self._wh,
            6: self._wi,
            7: self._wj,
            8: self._we,
            9: self._wf,
            10: self._wc,
            11: self._ws,
            12: self._wp,             
            13: self._wmms,
            14: self._wd,
            15: self._wdt,
            16: self._wn,                        
            17: self._wmms,
            18: self._wmms,
            19: self._wt
            }
        
        if t < 0 :
            writeType[t](x, message)
            return
        
        if t == 99:
            self._write(x.x, message)
            self._write(x.y, message)
            return
        
        message.fromstring(struct.pack('b', 0))
        
        if t == 98:
            message.fromstring(struct.pack('b', 99))
            self._write(x.x, message)
            self._write(x.y, message)
            return
        
        n = self.n(x)
        message.fromstring(struct.pack('>i', n))
        
        for i in range(0, n):
            writeType[t](x[i], message)
            
    def k(self, query, args=None):
        global SYNC
        if isinstance(query, str) and args is None: 
            self._send(SYNC, array.array('c',query))
        else:
            stuff = [array.array('c',query),]
            for item in args:
                stuff.append(item)
            self._send(SYNC, stuff )
        return self._readFromServer()

    def ks(self, query, args=None):
        global ASYNC
        if isinstance(query, str) and args is None: 
            self._send(ASYNC, array.array('c',query))
        else:
            stuff = [array.array('c',query),]
            for item in args:
                stuff.append(item)
            self._send(ASYNC, stuff )

    def kr(self):
        return self._readFromServer()

    def qt(self,x):
        return _qtype(x)

    def _send(self, sync, query):
        n = self._nx(query) + 8
        if sync:
            message = array.array('B', [0,1,0,0]) # 1 for synchronous requests
        else:
            message = array.array('B', [0,0,0,0]) # 1 for synchronous requests
        message.fromstring(struct.pack('>i', n)) # n is the total lengh of the message ( in bytes)
        self._write(query, message)
        if self.compress and (len(message) > 2000) and not self.localhost:
            self._z(message)
        #print ("[WRITE] KDB - message size: " + str(sys.getsizeof(message)))
        self.sock.send(message.tostring())
       
    def _readFromServer(self):
        """read the response from the server"""
        header = self.sock.recv(8)
        little_endian = struct.unpack('b', header[0:1])[0] == 1  #byte order
        zip = struct.unpack('b', header[2:3])[0] == 1  #compression
        self.offset = 4
        dataSize = self._ri(little_endian, header)
        
        inputBytes = self.recv_size(self.sock, dataSize - 8)
        #print ("[READ] KDB - message size: " + str(sys.getsizeof(header+inputBytes)))
        if zip:
            inputBytes = self._u(little_endian,inputBytes)
        else:
            self.offset = 0
        
        if struct.unpack('b', inputBytes[0:struct.calcsize('b')])[0] == -128 :
            self.offset = 1
            raise Exception(self._rs(little_endian, inputBytes))
        return self._r(little_endian, inputBytes)
    
    def recv_size(self, the_socket, size):
        """read size bytes from the socket."""
        #data length is packed into 8 bytes
        total_len=0;total_data=[]
        sock_data='';recv_size=min(size,8192)
        while total_len<size:
            sock_data=the_socket.recv(recv_size)
            total_data.append(sock_data)
            total_len=sum([len(i) for i in total_data ])
        return ''.join(total_data)

    def _endian_decide(self,little_endian,fmt):
        """pick between two types for conversion based on endianness"""
        if little_endian:
            return fmt
        else:
            return '>'+fmt
    
    def _rb(self, little_endian, bytearray):
        """retrieve byte from bytearray at offset"""
        val = struct.unpack('b', bytearray[self.offset:self.offset+1])[0]
        self.offset+=1
        return val
        
    def _rg(self, little_endian, bytearray):
        """retrieve byte from bytearray at offset"""
        val = UUID( bytes=array.array('B',bytearray[self.offset:self.offset+16]).tostring())
        self.offset+=1
        return val
        
    def _rc(self, little_endian, bytearray):
        """retrieve char from bytearray at offset"""
        val = struct.unpack('c', bytearray[self.offset:self.offset+1])[0]
        self.offset+=1
        return val
    
    def _ri(self, little_endian, bytearray):
        """retrieve integer from bytearray at offset"""
        val = struct.unpack(self._endian_decide(little_endian,'i'), bytearray[self.offset:self.offset+4])[0]
        self.offset+=4
        return val
    
    def _rd(self, little_endian, bytearray):
        """retrieve date from bytearray at offset"""
        val = struct.unpack(self._endian_decide(little_endian,'i'), bytearray[self.offset:self.offset+4])[0]
        self.offset+=4
        delta=datetime.timedelta(milliseconds=8.64e7*val)
        return datetime.date.fromtimestamp(self.gl(946684800L)) + delta  #946684800L is conversion from UNIX epoch to KDB epoch
    
    def _rt(self, little_endian, bytearray):
        """retrieve time from bytearray at offset"""
        val = struct.unpack(self._endian_decide(little_endian,'i'), bytearray[self.offset:self.offset+4])[0]
        self.offset+=4
        return (datetime.datetime.fromordinal(1) + datetime.timedelta(milliseconds=val)).time()
     
    def _rdt(self, little_endian, bytearray):
        """retrieve datetime from bytearray at offset.  kdb stores dates relative to 2000.01.01"""
        val = struct.unpack(self._endian_decide(little_endian,'d'), bytearray[self.offset:self.offset+8])[0]
        self.offset+=8
        delta=datetime.timedelta(milliseconds=8.64e7*val)  #8.64e7 is milliseconds in a day
        return datetime.datetime.fromtimestamp(self.gl(946684800L)) + delta  #946684800L is conversion from UNIX epoch to KDB epoch

    def _rp(self, little_endian, bytearray):
        """retrieve timestamp from bytearray at offset.  kdb stores dates relative to 2000.01.01"""
        val = struct.unpack(self._endian_decide(little_endian,'q'), bytearray[self.offset:self.offset+8])[0]
        self.offset+=8
        delta=datetime.timedelta(microseconds=long(val/1000))
        res =  datetime.datetime(2000,1,1) + delta
        return timestamp.combine(res.date(),res.time())
    
    def _rn(self, little_endian, bytearray):
        """retrieve timestamp from bytearray at offset.  kdb stores dates relative to 2000.01.01"""
        val = struct.unpack(self._endian_decide(little_endian,'q'), bytearray[self.offset:self.offset+8])[0]
        self.offset+=8
        return datetime.timedelta(microseconds=long(val/1000))
    
    def _re(self, little_endian, bytearray):
        """retrieve float from bytearray at offset"""
        val = struct.unpack(self._endian_decide(little_endian,'f'), bytearray[self.offset:self.offset+4])[0]
        self.offset+=4
        return val
    
    def _rj(self, little_endian, bytearray):
        """retrieve long from bytearray at offset"""
        val = struct.unpack(self._endian_decide(little_endian,'q'), bytearray[self.offset:self.offset+8])[0]
        self.offset+=8
        return val
    
    def _rf(self, little_endian, bytearray):
        """retrieve double from bytearray at offset"""
        val = struct.unpack(self._endian_decide(little_endian,'d'), bytearray[self.offset:self.offset+8])[0]
        self.offset+=8
        return val
    
    def _rh(self, little_endian, bytearray):
        """retrieve integer from bytearray at offset"""
        val = struct.unpack(self._endian_decide(little_endian,'h'), bytearray[self.offset:self.offset+2])[0]
        self.offset+=2
        return val
    
    def _rs(self, little_endian, bytearray):
        """retrieve null terminated string from bytearray"""
        end = bytearray.find("\0",self.offset)
        val = bytearray[self.offset:end]
        self.offset = end+1
        return val
                   
    def _r(self, little_endian, bytearray):
        """General retrieve data from bytearray.  format is type number followed by data""" 
        t = self._rb(little_endian, bytearray)
        readType = {
            -1: lambda: self._rb(little_endian, bytearray),
            -2: lambda: self._rg(little_endian, bytearray),            
            -4: lambda: self._rb(little_endian, bytearray),
            -5: lambda: self._rh(little_endian, bytearray),
            -6: lambda: self._ri(little_endian, bytearray),
            -7: lambda: self._rj(little_endian, bytearray),
            -8: lambda: self._re(little_endian, bytearray),
            -9: lambda: self._rf(little_endian, bytearray),
            -10: lambda: self._rc(little_endian, bytearray),
            -11: lambda: self._rs(little_endian, bytearray),
            -12: lambda: self._rp(little_endian, bytearray),
            -13: lambda: Month(self._ri(little_endian, bytearray)),
            -14: lambda: self._rd(little_endian, bytearray),
            -15: lambda: self._rdt(little_endian, bytearray),
            -16: lambda: self._rn(little_endian, bytearray),            
            -17: lambda: Minute(self._ri(little_endian, bytearray)),
            -18: lambda: Second(self._ri(little_endian, bytearray)),
            -19: lambda: self._rt(little_endian, bytearray),
            0: lambda: self._r(little_endian, bytearray),
            2: lambda: self._rg(little_endian, bytearray),                        
            1: lambda: self._rb(little_endian, bytearray),
            4: lambda: self._rb(little_endian, bytearray),
            5: lambda: self._rh(little_endian, bytearray),
            6: lambda: self._ri(little_endian, bytearray),
            7: lambda: self._rj(little_endian, bytearray),
            8: lambda: self._re(little_endian, bytearray),
            9: lambda: self._rf(little_endian, bytearray),
            10: lambda: self._rc(little_endian, bytearray),
            11: lambda: self._rs(little_endian, bytearray),
            12: lambda: self._rp(little_endian, bytearray),
            13: lambda: Month(self._ri(little_endian, bytearray)),
            14: lambda: self._rd(little_endian, bytearray),
            15: lambda: self._rdt(little_endian, bytearray),
            16: lambda: self._rn(little_endian, bytearray),            
            17: lambda: Minute(self._ri(little_endian, bytearray)),
            18: lambda: Second(self._ri(little_endian, bytearray)),
            19: lambda: self._rt(little_endian, bytearray)
            }
        if t < 0 :
            #In this case the value is a scalar
            if readType.has_key(t) : return readType[t]()
        if t > 99 :
            if t == 100 :
                self._rs(little_endian, bytearray)
                return self._r(little_endian, bytearray)
            if t < 104 :
                if self._rb(little_endian, bytearray) == 0 and t == 101:
                    return None 
                else:
                    return "func"
            self.offset = len(bytearray)
            return "func"
        
        if t == 99:
            keys = self._r(little_endian, bytearray)
            values = self._r(little_endian, bytearray)
            return Dict(keys, values)
        
        self.offset+=1;
        
        if t == 98:
            return Flip(self._r(little_endian, bytearray))
        
        n=self._ri(little_endian, bytearray) #length of the array
        val = []
        for i in range(0, n):
            item = readType[t]()
            val.append( item )
        return val
    
    def _z(self, message ):
        msglen = len(message)
        tmp = array.array('B',([0]*(msglen/2))) # original header + half size buffer
        buf = message[0:4] + tmp
        self._write(len(message),buf) # write len of original buffer
        s=8;c=12;d=12;f=0;p=0;h=0;s0=0;h0=0;r=0;q=0
        t = len(message)
        i = 0
        a = [0] * 256
        while( s<t ):
            if i is 0:
                if d > (msglen/2) - 17:
                    return message # if less than 12 bytes to compress just return...
                i = 1
                buf[c] = (f &0xFF)
                c = d
                d += 1
                f = 0
            
            h=0xFF&(message[s]^message[s+1])
            p=a[h]
            g = (s > (t-3)) or (p is 0) or ((message[s]^message[p]) is not 0)
            if(s0 > 0):
                a[h0]=s0
                s0=0
            if(g):
                h0 = h
                s0 = s
                buf[d]=message[s]
                d+=1
                s+=1
            else:
                a[h] = s
                f|=i
                p+=2
                s+=2
                r = s
                q = min(s+255,t)
                while(message[p] == message[s]) and ((s+1) <q):
                    s+=1
                    p+=1
                s+=1
                buf[d] = (h& 0xFF)
                d+=1
                buf[d] = ((s-r) & 0xFF)
                d+=1
            i = (i*2)&0xFF
        buf[12] = (f&0xFF)
        tmp = array.array('B')
        self._write(d,tmp)
        buf[4:8] = tmp
        return buf
    
    def _u(self, little_endian, buf):
        buffer = array.array('b',buf)
        n=0; r=0; f=0; s=8; p=s
        i=0
        self.offset = 0
        sz = self._ri(little_endian, buffer)
        dst=[0]*sz
        d=self.offset
        aa=[0]*256
        while s<len(dst):
            if i is 0:
                f=0xFF&(buffer[d])
                d+=1
                i=1
            if (f&i)is not 0:
                r=aa[0xFF&(buffer[d])]
                d+=1
                dst[s]=dst[r]
                s+=1
                r+=1
                dst[s]=dst[r]
                s+=1
                r+=1
                n=0xFF&(buffer[d])
                d+=1
                for m in range(0,n):
                    dst[s+m]=dst[r+m]
            else:
                dst[s]=buffer[d]
                s+=1
                d+=1
            while p<(s-1):
                aa[(0xFF&(dst[p]))^(0xFF&(dst[p+1]))]=p
                p+=1
            if (f&i)is not 0:
                s+=n
                p=s
            i*=2
            if(i is 256):
                i=0
        self.offset = 8
        return array.array('b',dst)