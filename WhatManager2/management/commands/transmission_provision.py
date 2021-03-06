#!/usr/bin/env python
from optparse import make_option
import os
import os.path
import pwd
import subprocess
import json

from django.core.management.base import BaseCommand, CommandError

from WhatManager2.settings import TRANSMISSION_FILES_ROOT, TRANSMISSION_BIND_HOST
from WhatManager2.utils import read_text, write_text
from home import models


transmission_init_script_template = '''#!/bin/sh -e
### BEGIN INIT INFO
# Provides:          transmission-daemon-<<<name>>>
# Required-Start:    $local_fs $remote_fs $network
# Required-Stop:     $local_fs $remote_fs $network
# Default-Start:     2 3 4 5
# Default-Stop:      0 1 6
# Short-Description: Start or stop the transmission-daemon-<<<name>>>.
### END INIT INFO

NAME=transmission-daemon-<<<name>>>
DAEMON=<<<daemon_path>>>
ENABLE_DAEMON=1
USER=transmission-<<<name>>>
STOP_TIMEOUT=30
OPTIONS="-g <<<files_path>>>"

export PATH="${PATH:+$PATH:}/sbin"

[ -x $DAEMON ] || exit 0

[ -e /etc/default/$NAME ] && . /etc/default/$NAME

. /lib/lsb/init-functions

start_daemon () {
    if [ $ENABLE_DAEMON != 1 ]; then
        log_progress_msg "(disabled, see /etc/default/${NAME})"
    else
        start-stop-daemon --start \
        --chuid $USER --user $USER \
        $START_STOP_OPTIONS \
        --exec $DAEMON -- $OPTIONS
    fi
}

case "$1" in
    start)
        log_daemon_msg "Starting bittorrent daemon" "$NAME"
        start_daemon
        log_end_msg 0
        ;;
    stop)
        log_daemon_msg "Stopping bittorrent daemon" "$NAME"
        start-stop-daemon --stop --quiet --user $USER \
            --exec $DAEMON --retry $STOP_TIMEOUT \
            --oknodo
        log_end_msg 0
        ;;
    restart|force-reload)
        log_daemon_msg "Restarting bittorrent daemon" "$NAME"
        start-stop-daemon --stop --quiet --user $USER \
            --exec $DAEMON --retry $STOP_TIMEOUT \
            --oknodo
        start_daemon
        log_end_msg 0
        ;;
    status)
        status_of_proc "$DAEMON" "$NAME" && exit 0 || exit $?
        ;;
    *)
        echo "Usage: /etc/init.d/$NAME {start|stop|reload|force-reload|restart|status}"
        exit 2
        ;;
esac

exit 0
'''


def discover_transmission():
    try:
        return subprocess.check_output(['which', 'transmission-daemon']).strip()
    except subprocess.CalledProcessError:
        raise Exception(u'transmission-daemon was not found. '
                        u'Make sure "which transmission-daemon" returns the right thing.')


def get_transmission_init_script(name, files_path):
    return (
        transmission_init_script_template
        .replace('<<<daemon_path>>>', discover_transmission())
        .replace('<<<files_path>>>', files_path)
        .replace('<<<name>>>', name)
    )


transmission_settings_template = '''{
    "alt-speed-down": 2000,
    "alt-speed-enabled": false,
    "alt-speed-time-begin": 540,
    "alt-speed-time-day": 127,
    "alt-speed-time-enabled": false,
    "alt-speed-time-end": 1020,
    "alt-speed-up": 10000,
    "bind-address-ipv4": "<<<bind_ipv4_host>>>",
    "bind-address-ipv6": "::",
    "blocklist-enabled": false,
    "blocklist-url": "http://www.example.com/blocklist",
    "cache-size-mb": 16,
    "dht-enabled": true,
    "download-dir": "/mnt/tank/Torrent/What.CD",
    "download-queue-enabled": true,
    "download-queue-size": 8,
    "encryption": 1,
    "idle-seeding-limit": 30,
    "idle-seeding-limit-enabled": false,
    "incomplete-dir": "/var/lib/transmission-daemon/download",
    "incomplete-dir-enabled": false,
    "lpd-enabled": false,
    "message-level": 2,
    "peer-congestion-algorithm": "",
    "peer-id-ttl-hours": 6,
    "peer-limit-global": 1000,
    "peer-limit-per-torrent": 120,
    "peer-port": <<<peer_port>>>,
    "peer-port-random-high": 65535,
    "peer-port-random-low": 49152,
    "peer-port-random-on-start": false,
    "peer-socket-tos": "default",
    "pex-enabled": true,
    "port-forwarding-enabled": false,
    "preallocation": 1,
    "prefetch-enabled": 1,
    "queue-stalled-enabled": true,
    "queue-stalled-minutes": 5,
    "ratio-limit": 2,
    "ratio-limit-enabled": false,
    "rename-partial-files": true,
    "rpc-authentication-required": true,
    "rpc-bind-address": "0.0.0.0",
    "rpc-enabled": true,
    "rpc-password": "<<<rpc_password>>>",
    "rpc-port": <<<rpc_port>>>,
    "rpc-url": "/transmission/",
    "rpc-username": "transmission",
    "rpc-whitelist": "127.0.0.1",
    "rpc-whitelist-enabled": false,
    "scrape-paused-torrents-enabled": true,
    "script-torrent-done-enabled": false,
    "script-torrent-done-filename": "",
    "seed-queue-enabled": false,
    "seed-queue-size": 10,
    "speed-limit-down": 800,
    "speed-limit-down-enabled": false,
    "speed-limit-up": 100,
    "speed-limit-up-enabled": false,
    "start-added-torrents": false,
    "trash-original-torrent-files": false,
    "umask": 0,
    "upload-slots-per-torrent": 64,
    "utp-enabled": true
}
'''


def get_transmission_settings(bind_host, rpc_port, peer_port, rpc_password):
    return (
        transmission_settings_template
        .replace('<<<bind_ipv4_host>>>', bind_host)
        .replace('<<<rpc_port>>>', str(int(rpc_port)))
        .replace('<<<peer_port>>>', str(int(peer_port)))
        .replace('<<<rpc_password>>>', rpc_password)
    )


use_confirm = True


def confirm():
    global use_confirm
    if not use_confirm:
        return
    answer = raw_input('Enter y to continue: ')
    if answer != 'y':
        exit(1)


def user_exists(username):
    for user in pwd.getpwall():
        if user.pw_name == username:
            return True
    return False


def create_system_user(username):
    args = ['useradd', '-r', '-s', '/bin/false', username]
    if subprocess.call(args) != 0:
        raise Exception('useradd returned non-zero. args={0}'.format(args))


def check_transmission_settings(path, bind_host, rpc_port, peer_port, username='transmission',
                                umask=0):
    try:
        settings = json.loads(read_text(path))
    except:
        return False
    if settings.get('bind-address-ipv4') != bind_host:
        return False
    if settings.get('rpc-port') != rpc_port:
        return False
    if settings.get('peer-port') != peer_port:
        return False
    if settings.get('rpc-username') != username:
        return False
    if settings.get('umask') != umask:
        return False
    return True


class TransInstanceManager(object):
    def __init__(self, instance):
        self.instance = instance
        self.name = str(instance.name)
        self.service_name = 'transmission-daemon-{0}'.format(self.name)
        self.transmission_files_path = os.path.join(TRANSMISSION_FILES_ROOT, self.name)
        self.init_path = os.path.join('/etc/init.d', self.service_name)
        self.init_script = get_transmission_init_script(self.name, self.transmission_files_path)
        self.init_script_perms = 0755
        self.username = 'transmission-{0}'.format(self.name)
        self.settings_path = os.path.join(self.transmission_files_path, 'settings.json')
        self.settings_json = get_transmission_settings(
            TRANSMISSION_BIND_HOST, instance.port, instance.peer_port, instance.password)

    def write_init_script(self):
        write_text(self.init_path, self.init_script)
        os.chmod(self.init_path, self.init_script_perms)

    def write_settings(self):
        write_text(self.settings_path, self.settings_json)
        os.chmod(self.settings_path, 0777)

    def sync(self):
        if not os.path.isfile(self.init_path):
            print 'Creating init script for {0}'.format(self.name)
            confirm()
            self.write_init_script()
        if self.init_script != read_text(self.init_path):
            print 'Overwriting init script for {0}'.format(self.name)
            confirm()
            self.write_init_script()
        if (os.stat(self.init_path).st_mode & 0777) != self.init_script_perms:
            print 'Fixing init script permissions for {0}'.format(self.name)
            confirm()
            os.chmod(self.init_path, self.init_script_perms)
        if not user_exists(self.username):
            print 'Creating user {0} for {1}'.format(self.username, self.name)
            confirm()
            create_system_user(self.username)
        if not os.path.isfile(self.settings_path):
            print 'Creating transmission settings file for {0}'.format(self.name)
            confirm()
            if not os.path.isdir(self.transmission_files_path):
                os.makedirs(self.transmission_files_path)
                os.chmod(self.transmission_files_path, 0777)
            self.write_settings()
        if not check_transmission_settings(
                self.settings_path, TRANSMISSION_BIND_HOST, self.instance.port,
                self.instance.peer_port):
            print 'Fixing transmission settings file for {0}'.format(self.name)
            confirm()
            self.write_settings()

    def exec_init_script(self, action):
        args = ['service', self.service_name, action]
        if subprocess.call(args) != 0:
            print 'Warning! Service start returned non-zero. args={0}'.format(args)

    def start_daemon(self):
        self.exec_init_script('start')

    def stop_daemon(self):
        self.exec_init_script('stop')


def ensure_root():
    if os.geteuid() != 0:
        raise CommandError('You have to call this program as root.')


def ensure_replica_set_exists(zone):
    try:
        models.ReplicaSet.objects.get(zone=zone)
    except models.ReplicaSet.DoesNotExist:
        print u'There is no replica set with the name {0}. I will create one.'.format(zone)
        confirm()
        replica_set = models.ReplicaSet(
            zone=zone,
            name='master',
        )
        replica_set.save()


def ensure_replica_sets_exist():
    ensure_replica_set_exists(models.ReplicaSet.ZONE_WHAT)
    ensure_replica_set_exists(models.ReplicaSet.ZONE_BIBLIOTIK)


# TODO: Implement it in a non-ugly way!
def apply_options(options):
    global use_confirm
    use_confirm = options['confirm']


class Command(BaseCommand):
    option_list = BaseCommand.option_list + (
        make_option('--no-confirm',
                    action='store_false',
                    dest='confirm',
                    default=True,
                    help='Do not ask for confirmation'),
    )
    help = 'Provisions transmission instances'

    def handle(self, *args, **options):
        apply_options(options)
        ensure_root()
        ensure_replica_sets_exist()
        for instance in models.TransInstance.objects.order_by('name'):
            manager = TransInstanceManager(instance)
            manager.sync()
