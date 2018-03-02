#!/usr/bin/env python3
#
# References:
# https://github.com/mopidy/mopidy-mpris/blob/develop/mopidy_mpris/objects.py
# https://dbus.freedesktop.org/doc/dbus-python/doc/tutorial.html
# https://gist.github.com/caspian311/4676061
# https://github.com/majutsushi/bin/blob/master/mocp-mpris

import dbus
from dbus.mainloop.glib import DBusGMainLoop
import dbus.service
from gi.repository import GLib
import re
import signal
from subprocess import check_call, check_output, STDOUT, CalledProcessError
import sys
import time
import copy
import musicbrainzngs
import os
import pdb

BUS_NAME = 'org.mpris.MediaPlayer2.moc_mpris'
OBJECT_PATH = '/org/mpris/MediaPlayer2'
ROOT_IFACE = 'org.mpris.MediaPlayer2'
PLAYER_IFACE = 'org.mpris.MediaPlayer2.Player'
TRACKLIST_IFACE = 'org.mpris.MediaPlayer2.TrackList'
PLAYLISTS_IFACE = 'org.mpris.MediaPlayer2.Playlists'

musicbrainzngs.set_useragent("MOCP MPRIS bridge", "0.1", "http://github.com/progwolff/moc-mpris")

class Mocp(dbus.service.Object):
    
    
    properties = None
    
    mocp_info = {}
    
    current_time = 0

    connected = False
    
    
    def __init__(self, *args, **kwargs):
        
        remote = kwargs.get('remote', None)
        
        self.remote_user = remote.split('@')[0] if remote else None
        self.remote_address = remote.split('@')[-1] if remote else None
    
        self.remote_name = self.remote_address if self.remote_address else 'local'
        
        
        self.properties = {
            ROOT_IFACE: self._get_root_iface_properties(),
            PLAYER_IFACE: self._get_player_iface_properties(),
            #TRACKLIST_IFACE: self._get_tracklist_iface_properties(),
            #PLAYLISTS_IFACE: self._get_playlists_iface_properties(),
        }
        
        self.mocp_update(None)
        
        self.bus_name = dbus.service.BusName(BUS_NAME, dbus.SessionBus())
        dbus.service.Object.__init__(self, self.bus_name, OBJECT_PATH)
        self.connected = True
        self.loop = GLib.MainLoop()
        
        self.oldinfo = {}

    def run(self):
        
        self.current_time = time.time()
        
        self.loop.run()
        
    def _get_root_iface_properties(self):
        return {
            'CanQuit': (True, None),
            'Fullscreen': (False, None),
            'CanSetFullscreen': (False, None),
            'CanRaise': (True, None),
            # NOTE Change if adding optional track list support
            'HasTrackList': (False, None),
            'Identity': ('MOC ({})'.format(self.remote_name), None),
            #'DesktopEntry': ('moc_mpris', None),
        }

    def _get_player_iface_properties(self):
        return {
            'PlaybackStatus': (self.get_PlaybackStatus, None),
            'LoopStatus': ('None', None),
            'Rate': (1.0, self.set_Rate),
            'Shuffle': (False, None),
            'Metadata': (self.get_Metadata, None),
            'Volume': (self.get_Volume, self.set_Volume),
            'Position': (self.get_Position, None),
            'MinimumRate': (1.0, None),
            'MaximumRate': (1.0, None),
            'CanGoNext': (self.get_CanGoNext, None),
            'CanGoPrevious': (self.get_CanGoPrevious, None),
            'CanPlay': (self.get_CanPlay, None),
            'CanPause': (self.get_CanPause, None),
            'CanSeek': (self.get_CanSeek, None),
            'CanControl': (self.get_CanControl, None),
        }

    def _get_tracklist_iface_properties(self):
        return {
            'Tracks': ({'/org/mocp/track/1'}, None)
        }

    def _get_playlists_iface_properties(self):
        return {
            'PlaylistCount': (1, None),
            'Tracks': ({'/org/mocp/track/1'}, None)
        }

    @dbus.service.method(dbus_interface=dbus.PROPERTIES_IFACE,
                         in_signature='ss', out_signature='v')
    def Get(self, interface, prop):
        print(
            '%s.Get(%s, %s) called',
            dbus.PROPERTIES_IFACE, repr(interface), repr(prop))
    
        if int(self.current_time) < int(time.time()):
            if prop in ['Position']:
                self.mocp_update(PLAYER_IFACE, skipPosition=True)
            self.current_time = time.time()
    
        (getter, _) = self.properties[interface][prop]
        if callable(getter):
            return getter()
        else:
            return getter
    
    @dbus.service.method(dbus_interface=dbus.PROPERTIES_IFACE,
                         in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface):
        print(
            '%s.GetAll(%s) called', dbus.PROPERTIES_IFACE, repr(interface))
        getters = {}
        for key, (getter, _) in self.properties[interface].items():
            getters[key] = getter() if callable(getter) else getter
        return getters
    
    @dbus.service.method(dbus_interface=dbus.PROPERTIES_IFACE,
                         in_signature='ssv', out_signature='')
    def Set(self, interface, prop, value):
        print(
            '%s.Set(%s, %s, %s) called',
            dbus.PROPERTIES_IFACE, repr(interface), repr(prop), repr(value))
        _, setter = self.properties[interface][prop]
        if setter is not None:
            setter(value)
            self.PropertiesChanged(
                interface, {prop: self.Get(interface, prop)}, [])
            
    @dbus.service.signal(dbus_interface=dbus.PROPERTIES_IFACE,
                         signature='sa{sv}as')
    def PropertiesChanged(self, interface, changed_properties,
                          invalidated_properties):
        print(
            '%s.PropertiesChanged(%s, %s, %s) signaled',
            dbus.PROPERTIES_IFACE, interface, changed_properties,
            invalidated_properties)
            
    @dbus.service.method(dbus_interface=ROOT_IFACE)
    def Quit(self):
        self.mocp_cmd(['-x'])
        sys.exit(0)
        
    @dbus.service.method(dbus_interface=ROOT_IFACE)
    def Raise(self):
        print('Raise called')
        folder = self.get_Metadata()['xesam:url']
        folder = os.path.split(folder)[0]
        self.raise_cmd(folder)

    @dbus.service.method(dbus_interface=PLAYER_IFACE)
    def Next(self):
        print('%s.Next called', PLAYER_IFACE)
        if not self.get_CanGoNext():
            print('%s.Next not allowed', PLAYER_IFACE)
            return
        self.mocp_cmd(['--next'])
        time.sleep(.5)
        self.mocp_update(PLAYER_IFACE)

    @dbus.service.method(dbus_interface=PLAYER_IFACE)
    def Previous(self):
        print('%s.Previous called', PLAYER_IFACE)
        if not self.get_CanGoPrevious():
            print('%s.Previous not allowed', PLAYER_IFACE)
            return
        self.mocp_cmd(['--previous'])
        time.sleep(.5)
        self.mocp_update(PLAYER_IFACE)

    @dbus.service.method(dbus_interface=PLAYER_IFACE)
    def Pause(self):
        print('%s.Pause called', PLAYER_IFACE)
        if not self.get_CanPause():
            print('%s.Pause not allowed', PLAYER_IFACE)
            return
        self.mocp_cmd(['--pause'])
        self.mocp_update(PLAYER_IFACE)

    @dbus.service.method(dbus_interface=PLAYER_IFACE)
    def PlayPause(self):
        self.mocp_update(PLAYER_IFACE)
        print('%s.PlayPause called', PLAYER_IFACE)
        if not self.get_CanPause():
            print('%s.PlayPause not allowed', PLAYER_IFACE)
            return
        state = self.get_PlaybackStatus()
        if state == 'Playing':
            self.Pause()
        elif state == 'Paused':
            self.Play()
        elif state == 'Stopped':
            self.Play()

    @dbus.service.method(dbus_interface=PLAYER_IFACE)
    def Stop(self):
        print('%s.Stop called', PLAYER_IFACE)
        if not self.get_CanControl():
            print('%s.Stop not allowed', PLAYER_IFACE)
            return
        self.mocp_cmd(['--stop'])
        self.mocp_update(PLAYER_IFACE)

    @dbus.service.method(dbus_interface=PLAYER_IFACE)
    def Play(self):
        print('%s.Play called', PLAYER_IFACE)
        if not self.get_CanPlay():
            print('%s.Play not allowed', PLAYER_IFACE)
            return
        self.mocp_cmd(['--unpause'])
        self.mocp_update(PLAYER_IFACE)

    @dbus.service.method(dbus_interface=PLAYER_IFACE)
    def Seek(self, microseconds):
        print('%s.Seek called', PLAYER_IFACE)
        if not self.get_CanSeek():
            print('%s.Seek not allowed', PLAYER_IFACE)
            return
        offset_in_seconds = microseconds / 1000. / 1000.
        self.mocp_cmd(['--seek', offset_in_seconds])

    @dbus.service.method(dbus_interface=PLAYER_IFACE)
    def SetPosition(self, track_id, microseconds):
        print('%s.SetPosition called', PLAYER_IFACE)
        if not self.get_CanSeek():
            print('%s.SetPosition not allowed', PLAYER_IFACE)
            return
        position_in_seconds = microseconds / 1000. / 1000.
        self.mocp_cmd(['--jump', str(int(position_in_seconds))+'s'])
        
    # --- Player interface signals

    @dbus.service.signal(dbus_interface=PLAYER_IFACE, signature='x')
    def Seeked(self, microseconds):
        print('%s.Seeked signaled', PLAYER_IFACE)
        # Do nothing, as just calling the method is enough to emit the signal.

    # --- Player interface properties

    def get_PlaybackStatus(self):
        state = self.get_mocp_info("State")

        if state is None or state == 'STOP':
            return 'Stopped'
        if state == 'PLAY':
            return 'Playing'
        elif state == 'PAUSE':
            return 'Paused'
        else:
            return 'Stopped'

    def get_LoopStatus(self):
        repeat = self.core.tracklist.repeat.get()
        single = self.core.tracklist.single.get()
        if not repeat:
            return 'None'
        else:
            if single:
                return 'Track'
            else:
                return 'Playlist'

    def set_LoopStatus(self, value):
        if not self.get_CanControl():
            print('Setting %s.LoopStatus not allowed', PLAYER_IFACE)
            return
        if value == 'None':
            self.core.tracklist.repeat = False
            self.core.tracklist.single = False
        elif value == 'Track':
            self.core.tracklist.repeat = True
            self.core.tracklist.single = True
        elif value == 'Playlist':
            self.core.tracklist.repeat = True
            self.core.tracklist.single = False

    def set_Rate(self, value):
        if not self.get_CanControl():
            # NOTE The spec does not explictly require this check, but it was
            # added to be consistent with all the other property setters.
            print('Setting %s.Rate not allowed', PLAYER_IFACE)
            return
        if value == 0:
            self.Pause()

    def get_Shuffle(self):
        return self.core.tracklist.random.get()

    def set_Shuffle(self, value):
        if not self.get_CanControl():
            print('Setting %s.Shuffle not allowed', PLAYER_IFACE)
            return
        if value:
            self.core.tracklist.random = True
        else:
            self.core.tracklist.random = False
            
    def get_AlbumArt(self, artist, album):
        try:
            result = musicbrainzngs.search_releases(artist=artist, release=album, limit=5)
        except Exception as e:
            return ''
        for r in result['release-list']:
            try:
                data = musicbrainzngs.get_image_list(r['id'])
                return data['images'][0]['image']
            except Exception as e:
                continue

    def get_Metadata(self):
        
        metadata = {}
        metadata['mpris:trackid'] = '/org/moc_mpris/track/1'
        metadata['mpris:length'] = int(self.get_mocp_info("TotalSec", 0))*1000000
        metadata['xesam:url'] = self.get_mocp_info("File")
        metadata['xesam:title'] = self.get_mocp_info("SongTitle")
        metadata['xesam:artist'] = self.get_mocp_info("Artist")
        metadata['xesam:album'] = self.get_mocp_info("Album")
        metadata['mpris:artUrl'] = self.get_AlbumArt(metadata['xesam:artist'], metadata['xesam:album'])

        metadata = {i:metadata[i] for i in metadata if metadata[i] is not None}

        return dbus.Dictionary(metadata, signature='sv')

    def get_Volume(self):
        volume = int(self.alsa_get_volume_cmd())
        if volume is None:
            return 0
        return volume / 100.0

    def set_Volume(self, value):
        if not self.get_CanControl():
            print('Setting %s.Volume not allowed', PLAYER_IFACE)
            return
        if value is None:
            return
        elif value < 0:
            volume = 0
        elif value > 1:
            volume = 100
        elif 0 <= value <= 1:
            volume = int(value * 100)
        self.mocp_cmd(['--volume', str(volume)])

    def get_Position(self):
        print(int(self.get_mocp_info('CurrentSec', 0))*1000000)
        return int(self.get_mocp_info('CurrentSec', 0))*1000000

    def get_CanGoNext(self):
        if not self.get_CanControl():
            return False
        return self.get_mocp_info("SongTitle") is not None

    def get_CanGoPrevious(self):
        if not self.get_CanControl():
            return False
        return self.get_mocp_info("SongTitle") is not None

    def get_CanPlay(self):
        if not self.get_CanControl():
            return False
        return self.get_mocp_info("SongTitle") is not None

    def get_CanPause(self):
        if not self.get_CanControl():
            return False
        # NOTE Should be changed to vary based on capabilities of the current
        # track if Mopidy starts supporting non-seekable media, like streams.
        return True

    def get_CanSeek(self):
        if not self.get_CanControl():
            return False
        # NOTE Should be changed to vary based on capabilities of the current
        # track if Mopidy starts supporting non-seekable media, like streams.
        return True

    def get_CanControl(self):
        # NOTE This could be a setting for the end user to change.
        return True
    
    def mocp_update(self, interface, skipPosition=False):
        
        if not self.update_mocp_info():
            sys.exit(0)
        
        getters = {}
        
        if interface is not None:
            for key, (getter, _) in self.properties[interface].items():
                getters[key] = getter() if callable(getter) else getter
            
            
            print(getters.keys())
            print(self.oldinfo.keys())
            
            ret = copy.copy(getters)
            for key in getters.keys():
                if key in self.oldinfo.keys():
                    if self.oldinfo[key] == getters[key]:
                        print("{} == {}".format(self.oldinfo[key],getters[key]))
                        del ret[key]
        
            if skipPosition:
                del ret['Position']
        
            if len(ret.keys()) > 0:
                self.PropertiesChanged(interface, ret, [])
            
            self.oldinfo = copy.copy(getters)
        
    def update_mocp_info(self):
        try:
            self.mocp_info = {}
            info = self.mocp_cmd(['-i'])
            for line in info.splitlines():
                match = re.match('^(.*):', line)
                if match:
                    key = match.group(1)
                else:
                    continue
                match = re.match('[^:]*: (.*)', line)
                if match:
                    val = match.group(1)
                else:
                    continue
                self.mocp_info[key] = val
                
            if not 'State' in self.mocp_info.keys() or self.mocp_info['State'] == 'STOP':
                return False
            
            return True
                
        except CalledProcessError as e:
            return True
    
    def get_mocp_info(self, reg, default=None):
        if reg in self.mocp_info.keys():
            return self.mocp_info[reg]
        return default

    def mocp_cmd(self, args):
        
        try:
            print(args)
            print('{}@{}'.format(self.remote_user,self.remote_address))
            if self.remote_address is not None:
                if self.remote_user is not None:
                    return check_output(['ssh', '{}@{}'.format(self.remote_user,self.remote_address), 'mocp'] + args, stderr=STDOUT).decode('utf-8')
                else:
                   return check_output(['ssh', '{}'.format(self.remote_address), 'mocp'] + args, stderr=STDOUT).decode('utf-8') 
            else:
                return check_output(['mocp'] + args, stderr=STDOUT).decode('utf-8')
    
        except CalledProcessError as e:
            return ''
        
    def alsa_get_volume_cmd(self):
        
        try:
            if self.remote_address is not None:
                if self.remote_user is not None:
                    alsainfo = check_output(['ssh', '{}@{}'.format(self.remote_user,self.remote_address), 'amixer', 'get', 'Digital'], stderr=STDOUT).decode('utf-8')
                else:
                    alsainfo = check_output(['ssh', '{}'.format(self.remote_address), 'amixer', 'get', 'Digital'], stderr=STDOUT).decode('utf-8')
            else:
                alsainfo = check_output(['amixer', 'get', 'Digital'], stderr=STDOUT).decode('utf-8')
            for line in alsainfo.splitlines():
                match=re.search('([0-9]*)%', line)
                if match:
                    return match.group(1)
            return 0
        except CalledProcessError as e:
            print(e)
            return 0

    def raise_cmd(self, folder):
        try: 
            print(folder)
            if self.remote_address:
                if self.remote_user:
                    out = check_output(['ssh', '-X', '{}@{}'.format(self.remote_user,self.remote_address), "xterm -fg white -bg black -e mocp -m -O MusicDir='{}' ; sleep 10".format(folder)]).decode('utf-8')
                else:
                    out = check_output(['ssh', '-X', '{}'.format(self.remote_address), "xterm -fg white -bg black -e mocp -m -O MusicDir='{}' ; sleep 10".format(folder)]).decode('utf-8')
            else:
                out = check_output(["xterm -fg white -bg black -e mocp -m -O MusicDir='{}' ; sleep 10".format(folder)]).decode('utf-8')
            print(out)
        except CalledProcessError as e:
            print(e)

def main():
    DBusGMainLoop(set_as_default=True)

    remote = None if len(sys.argv) < 2 else sys.argv[1]
    Mocp(remote=remote).run()

if __name__ == '__main__':
    sys.exit(main())
