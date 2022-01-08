class DataParser:

    class ParserException(Exception):
        def __init__(self, *args):
            if args:
                parser = args[0]  # type: DataParser
                text = args[1]  # type: str
                self.message = text + '\n'
                parse_str = parser.data.hex()
                ptr = parser.cursor
                self.message += parse_str[:ptr*2] + '|' + \
                                parse_str[ptr*2:(ptr+1)*2] + '|' + \
                                parse_str[(ptr+1)*2:]
            else:
                self.message = None

        def __str__(self):
            if self.message:
                return 'ParserException, {}'.format(self.message)
            else:
                return 'ParserException raised'

    def __init__(self, data: bytes):
        self.data = bytes(data) if data else data
        self.cursor = 0
        self.length = len(data) if data else 0

    def _assert_space(self, length: int):
        if self.cursor + length > self.length:
            raise self.ParserException(self, f'Out of bounds: trying to read {length} byte(s) {self.cursor} {self.length} {len(self.data)}')

    def read_byte(self):
        self._assert_space(1)
        data = self.data[self.cursor]
        self.cursor += 1
        return bytes([data])

    def read_int(self):
        return self.read_byte()[0]

    def read_boolean(self):
        data = self.read_byte()
        if data not in (b'\0', b'\x01'):
            raise self.ParserException(self, 'Not a boolean')
        return False if data[0] == 0 else True

    def read_bytes(self, length: int):
        self._assert_space(length)
        data = self.data[self.cursor:self.cursor + length]
        self.cursor += length
        return data

    def read_var_bytes(self):
        length = self.read_byte()[0]
        return self.read_bytes(length)

    def read_var_bytes_tuple(self):
        length = self.read_byte()[0]
        return length, self.read_bytes(length)

    def read_var_bytes_tuple_bytes(self):
        length = self.read_byte()[0]
        return bytes([length]), self.read_bytes(length)

    def read_bytes_as_ascii(self, length: int):
        return self.read_bytes(length).decode('ascii')

    def read_var_bytes_as_ascii(self):
        return self.read_var_bytes().decode('ascii')

    def read_var_bytes_as_ascii_tuple(self):
        length, data = self.read_var_bytes_tuple()
        return length, data.decode('ascii')

    def read_var_bytes_as_ascii_tuple_bytes(self):
        length, data = self.read_var_bytes_tuple_bytes()
        return length, data.decode('ascii')

    def is_finished(self):
        if self.data is None:
            return True
        else:
            return self.cursor >= self.length - 1

    def print_loc(self):
        parse_str = self.data.hex()
        ptr = self.cursor
        message = parse_str[:ptr*2] + '|' + \
                        parse_str[ptr*2:(ptr+1)*2] + '|' + \
                        parse_str[(ptr+1)*2:]
        print(message)

def main():
    data = '085155414c544553540b245445535441535345543104000000010000005574120000ff085155414c544553540b2454455354415353455431040000000100000055741200007801'
    data_parser = DataParser(bytes.fromhex(data))
    while not data_parser.is_finished():
        qual_len, qual = data_parser.read_var_bytes_tuple_bytes()
        data_parser.print_loc()
        restricted_len, restricted = data_parser.read_var_bytes_tuple_bytes()
        data_parser.print_loc()
        idx_txnumb = data_parser.read_bytes(4 + 4 + 5)
        data_parser.print_loc()
        flag = data_parser.read_byte()
        data_parser.print_loc()
        print('DONE')
            

if __name__ == '__main__':
    main()