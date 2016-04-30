import logging
from threading import Thread
from time import sleep

from mahjong.client import Client
from tenhou.decoder import TenhouDecoder

logger = logging.getLogger('tenhou')


class TenhouClient(Client):
    socket = None
    game_is_continue = True
    keep_alive_thread = None

    decoder = TenhouDecoder()

    def __init__(self, socket):
        super().__init__()
        self.socket = socket

    def authenticate(self):
        self._send_message('<HELO name="NoName" tid="f0" sx="M" />')
        auth_message = self._read_message()

        auth_string = self.decoder.parse_auth_string(auth_message)
        if not auth_string:
            return False

        auth_token = self.decoder.generate_auth_token(auth_string)

        self._send_message('<AUTH val="{0}"/>'.format(auth_token))
        self._send_message('<PXR V="0" />')
        self._send_message('<PXR V="1" />')

        message = self._read_message()
        if '<ln' in message:
            self._send_keep_alive_ping()
            logger.info('Successfully authenticated')
            return True
        else:
            return False

    def start_the_game(self):
        log = ''
        game_started = False
        self._send_message('<JOIN t="0,1" />')
        logger.info('Looking for the game...')

        while not game_started:
            sleep(1)

            messages = self._get_multiple_messages()

            for message in messages:

                if '<rejoin' in message:
                    # game wasn't found, continue to wait
                    self._send_message('<JOIN t="0,1,r" />')

                if '<go' in message:
                    self._send_message('<GOK />')
                    self._send_message('<NEXTREADY />')

                if '<taikyoku' in message:
                    game_started = True
                    log = self.decoder.parse_log_link(message)

        logger.info('Game started')
        logger.info('Log: {0}'.format(log))
        logger.info('Players: {0}'.format(self.table.players))

        while self.game_is_continue:
            sleep(1)

            messages = self._get_multiple_messages()

            for message in messages:

                if '<init' in message:
                    values = self.decoder.parse_initial_values(message)
                    self.table.init_round(
                        values['round_number'],
                        values['count_of_honba_sticks'],
                        values['count_of_riichi_sticks'],
                        values['dora'],
                        values['dealer'],
                        values['scores'],
                    )

                    logger.info('Players: {0}'.format(self.table.get_players_sorted_by_scores()))

                if '<un' in message:
                    values = self.decoder.parse_names_and_ranks(message)
                    self.table.set_players_names_and_ranks(values)

                # draw and discard
                if '<t' in message:
                    tile = self.decoder.parse_tile(message)
                    self.draw_tile(tile)
                    sleep(1)

                    tile = self.discard_tile()
                    # tenhou format: <D p="133" />
                    self._send_message('<D p="{0}"/>'.format(tile))

                # the end of round
                if 'agari' in message or 'ryuukyoku' in message:
                    sleep(2)
                    self._send_message('<NEXTREADY />')

                open_sets = ['t="1"', 't="2"', 't="3"', 't="4"', 't="5"']
                if any(i in message for i in open_sets):
                    sleep(1)
                    self._send_message('<N />')

                # set call
                if '<n who=' in message:
                    meld = self.decoder.parse_meld(message)
                    self.call_meld(meld)

                other_players_discards = ['<e', '<f', '<g']
                if any(i in message for i in other_players_discards):
                    tile = self.decoder.parse_tile(message)

                    if '<e' in message:
                        player_seat = 1
                    elif '<f' in message:
                        player_seat = 2
                    else:
                        player_seat = 3

                    self.enemy_discard(player_seat, tile)

                if 'owari' in message:
                    values = self.decoder.parse_final_scores_and_uma(message)
                    self.table.set_players_scores(values['scores'], values['uma'])

                if '<prof' in message:
                    self.game_is_continue = False

        logger.info('Players: {0}'.format(','.join(self.table.get_players_sorted_by_scores())))

        self.end_the_game()

    def end_the_game(self):
        self._send_message('<BYE />')
        self.socket.close()

        self.keep_alive_thread.join()

        logger.info('End of the game')

    def _send_message(self, message):
        # tenhou required the empty byte in the end of each sending message
        logger.debug('Send: {0}'.format(message))
        message += '\0'
        self.socket.sendall(message.encode())

    def _read_message(self):
        message = self.socket.recv(1024)
        logger.debug('Get: {0}'.format(message.decode('utf-8').replace('\x00', ' ')))

        message = message.decode('utf-8')
        # sometimes tenhou send messages in lower case, sometime in upper case, let's unify the behaviour
        message = message.lower()

        return message

    def _get_multiple_messages(self):
        # tenhou can send multiple messages in one request
        messages = self._read_message()
        messages = messages.split('\x00')
        # last message always is empty after split, so let's exclude it
        messages = messages[0:-1]

        return messages

    def _send_keep_alive_ping(self):
        def send_request():
            while self.game_is_continue:
                self._send_message('<Z />')
                sleep(15)

        self.keep_alive_thread = Thread(target=send_request)
        self.keep_alive_thread.start()
