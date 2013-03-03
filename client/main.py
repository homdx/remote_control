# encoding: utf-8
from kivy.app import App
from kivy.properties import ListProperty, ObjectProperty, StringProperty,\
    NumericProperty, BooleanProperty, DictProperty
from kivy.support import install_twisted_reactor

from kivy.metrics import sp
from kivy.clock import Clock

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.uix.dropdown import DropDown

# installing twisted reactor the kivy way before importing anything from
# twisted
install_twisted_reactor()

from twisted.internet import reactor
from twisted.internet.protocol import Factory, Protocol
from twisted.internet.endpoints import TCP4ClientEndpoint

from ConfigParser import ConfigParser
from functools import partial
from shutil import move

from json import JSONDecoder
from json import JSONEncoder

json_decode = JSONDecoder().raw_decode
json_encode = JSONEncoder().encode

__version__ = '0.01'

CONFIG = 'remote_command.cfg'


class CommandClient(Protocol):
    def sendMessage(self, msg):
        self.transport.write(msg)

    def dataReceived(self, data):
        app.receive(data)


class CommandClientFactory(Factory):
    def buildProtocol(self, *args):
        return CommandClient()


class MousePad(Widget):
    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            touch.grab(self)
            if touch.is_double_tap:
                touch.ud['pressed'] = True
                app.mouse_press()
            return True
        return super(MousePad, self).on_touch_down(touch)

    def on_touch_move(self, touch):
        if touch.grab_current == self:
            app.mouse_move(touch.dx, touch.dy)
            return True
        return super(MousePad, self).on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch.grab_current == self:
            touch.ungrab(self)
            if 'pressed' in touch.ud:
                app.mouse_release()
            return True
        return super(MousePad, self).on_touch_up(touch)


class RemoteCommand(App):
    commands = ListProperty([])
    container = ObjectProperty(None)
    status = ObjectProperty(None)
    log = StringProperty('')
    mouse_sensivity = NumericProperty(1)
    screen_texture = ObjectProperty(None)
    capture_fps = NumericProperty(0)
    image_size = NumericProperty(128)
    dropdown = ObjectProperty(None)
    mods = DictProperty({})

    def connect(self, ip, port):
        if self.dropdown:
            self.dropdown.dismiss()

        point = TCP4ClientEndpoint(reactor, ip, int(port))
        d = point.connect(CommandClientFactory())
        d.addCallback(self.got_protocol)
        self.log += u"trying to connect…\n"
        self.leftover = ''

        if ip not in self.config.items('connections'):
            self.config.set('connections', ip, str(port))

    def send(self, *args, **kwargs):
        self.protocol.sendMessage(json_encode(kwargs))

    def update_screen(self, *args):
        self.send(command='capture',
                  size=(self.image_size, self.image_size))

    def propose_addresses(self, address_input):
        if address_input.focus:
            if not self.dropdown:
                self.dropdown = DropDown()
                #self.dropdown.bind(on_select=self.complete_address)
            else:
                self.dropdown.clear_widgets()

            connections = self.config.items('connections')

            for c in connections:
                if c[0].startswith(address_input.text):
                    lbl = Button(text=':'.join(c), size_hint_y=None,
                                 height=sp(30))
                    lbl.bind(on_press=lambda x:
                             self.connect(*x.text.split(':')))
                    self.dropdown.add_widget(lbl)

            self.dropdown.open(address_input.parent)

    def receive(self, stream):
        #if len(stream) > 1000: import ipdb; ipdb.set_trace()
        stream = self.leftover + stream
        while stream:
            try:
                datadict, index = json_decode(stream)
                self.leftover = ''
            except ValueError:
                # incomplete data, keep to analyse later
                self.leftover = stream
                return

            stream = stream[index:]

            #self.log += 'got data: %s\n' % datadict
            if not isinstance(datadict, dict):
                # something went wrong, gtfo for now, FIXME
                print "didn't get a dict for datadict"
                return

            if 'commands' in datadict:
                self.commands = datadict['commands']

            if 'status' in datadict:
                status = datadict['status']
                self.status.clear_widgets()

                for uid, command in status.items():
                    box = BoxLayout(size_hint_y='None', height='30dp')
                    label = Label(text=' '.join(command['command']))
                    button = Button(text='x')
                    button.bind(on_press=partial(
                        self.send, command='kill', uid=uid))
                    box.add_widget(label)
                    box.add_widget(button)
                    self.status.add_widget(box)

            if 'image' in datadict:
                #print "receiving capture"
                uid = datadict['image']
                if uid not in self.images:
                    fn = 'tmp-%s.bmp' % uid
                    self.images[uid] = [
                        fn,  # filename
                        open(fn, 'w'),  # file descriptor
                        0,  # next expected chunk
                        {}  # chunks arrived too early
                    ]

                fn, f, c, chunks = self.images[uid]

                data = datadict['data']
                chunk = datadict['chunk']
                #print'receiving %s chunk %s data %s' % (uid, chunk, data[:10])

                if chunk == c:
                    if not data:
                        #print "empty chunk, closing"
                        f.close()
                        move(fn, 'tmp.bmp')
                        self.screen_texture.reload()
                        del self.images[uid]

                    else:
                        f.write(datadict['data'].decode('base64'))
                        #print "writting chunk", c
                        c += 1

                else:
                    chunks[chunk] = data.decode('base64')

                while c in chunks:
                    #print "applying chunk %s that we got before" % c
                    f.write(chunks[c])
                    c += 1

                if data:
                    self.images[uid] = fn, f, c, chunks

    def got_protocol(self, p):
        self.log += "got protocol\n"
        self.protocol = p
        self.send(command='list')
        print "adding schedule"

    def on_capture_fps(self, *args):
        Clock.unschedule(self.update_screen)
        Clock.schedule_interval(self.update_screen, 1 / self.capture_fps)

    def on_commands(self, *args):
        self.container.clear_widgets()
        self.log += 'got a list of commands!\n'
        for command, arguments in self.commands:
            box = BoxLayout(height=sp(30))
            button = Button(text=command)

            args_inputs = []
            for arg in arguments.split(','):
                if arg == 'str':
                    txtinput = TextInput()
                    args_inputs.append(txtinput)
                    box.add_widget(txtinput)

            button.bind(on_press=partial(
                self.execute, command, arguments.split(','), args_inputs))
            box.add_widget(button)

            self.container.add_widget(box)

    def execute(self, command, arguments, args_inputs, *args):
        values = []
        for arg_type, arg_input in zip(arguments, args_inputs):
            if arg_type == 'str':
                values.append(arg_input.text)

        self.send(run=command, arguments=values)

    def mouse_move(self, dx, dy):
        self.send(command='mouse', action='move',
                  dx=int(round(dx * self.mouse_sensivity)),
                  dy=-int(round(dy * self.mouse_sensivity)))

    def mouse_click(self, b=1, n=2):
        self.send(command='mouse', action='click', b=b, n=n)

    def mouse_press(self, b=1):
        self.log += 'mouse pressed\n'
        self.send(command='mouse', action='press', b=b)

    def mouse_release(self, b=1):
        self.log += 'mouse released\n'
        self.send(command='mouse', action='release', b=b)

    def send_keys(self, string):
        self.send(command='type', string=string)

    def press_special_key(self, key):
        self.send(command='press_key', key=key.text)

    def release_special_key(self, key):
        self.send(command='release_key', key=key.text)

    def on_start(self, *args):
        self.config = ConfigParser()
        self.config.read(CONFIG)
        self.images = {}

        if not self.config.has_section('connections'):
            self.config.add_section('connections')

            with open(CONFIG, 'w') as f:
                self.config.write(f)

    def on_pause(self, *args):
        with open(CONFIG, 'w') as f:
            self.config.write(f)
        return True

    def on_resume(self, *args):
        return True

    def on_stop(self, *args):
        with open(CONFIG, 'w') as f:
            self.config.write(f)
        return True

    def populate_keyboard(self, grid):
        b = Button(text='escape')
        b.bind(on_press=self.press_special_key)
        b.bind(on_release=self.release_special_key)
        grid.add_widget(b)

        for f in range(12):
            b = Button(text='f%d' % (f + 1))
            b.bind(on_press=self.press_special_key)
            b.bind(on_release=self.release_special_key)
            grid.add_widget(b)

        for i in range(13):
            grid.add_widget(Widget())

        grid.add_widget(Widget())

        b = Button(text='up')
        b.bind(on_press=self.press_special_key)
        b.bind(on_release=self.release_special_key)
        grid.add_widget(b)

        for i in range(2):
            grid.add_widget(Widget())

        for t in 'home', 'end':
            b = Button(text=t)
            b.bind(on_press=self.press_special_key)
            b.bind(on_release=self.release_special_key)
            grid.add_widget(b)

        grid.add_widget(Widget())

        b = Button(text='shift')
        grid.add_widget(b)
        self.mods['shift'] = b

        b = Button(text='control')
        grid.add_widget(b)
        self.mods['control'] = b

        b = Button(text='alt')
        grid.add_widget(b)
        self.mods['alt'] = b

        b = Button(text='meta')
        grid.add_widget(b)
        self.mods['meta'] = b

        grid.add_widget(Widget())

        b = Button(text='backspace')
        b.bind(on_press=self.press_special_key)
        b.bind(on_release=self.release_special_key)
        grid.add_widget(b)

        for i in range(5):
            grid.add_widget(Widget())

        for t in 'left', 'down', 'right':
            b = Button(text=t)
            b.bind(on_press=self.press_special_key)
            b.bind(on_release=self.release_special_key)
            grid.add_widget(b)

        grid.add_widget(Widget())

        for t in 'up', 'down':
            b = Button(text='pg%s' % t)
            b.bind(on_press=self.press_special_key)
            b.bind(on_release=self.release_special_key)
            grid.add_widget(b)

        grid.add_widget(Widget())

        b = Button(text='return')
        b.bind(on_press=self.press_special_key)
        b.bind(on_release=self.release_special_key)
        grid.add_widget(b)


if __name__ == '__main__':
    global app
    app = RemoteCommand()
    app.run()
