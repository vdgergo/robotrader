from datetime import datetime

class Trade:
    """
    Egyedi kereskedési pozíciót reprezentáló osztály.
    Az összes aktív (nyitott) pozíciót egy osztályszintű szótárban tárolja.
    """
    active_trades = {}
    max_trades = 0  # A maximális párhuzamos trade-ek száma, a Broker állítja be.

    def __init__(self, symbol, buy_price, buy_time, quantity):
        """
        Létrehoz egy új Trade példányt és hozzáadja az aktív kereskedésekhez.
        """
        if len(Trade.active_trades) >= Trade.max_trades:
            raise ValueError(f"Nem lehet új pozíciót nyitni. Az aktív trade-ek száma ({len(Trade.active_trades)}) elérte a maximumot ({Trade.max_trades}).")

        if symbol in Trade.active_trades:
            raise ValueError(f"Már létezik nyitott pozíció a következő párhoz: {symbol}")

        self.symbol = symbol
        self.buy_price = float(buy_price)
        self.buy_time = buy_time
        self.quantity = quantity
        self.last_price = buy_price
        self.quantity = float(quantity)
        self.last_price = float(buy_price)

        # A példány hozzáadása az osztályszintű szótárhoz
        Trade.active_trades[self.symbol] = self
        print(f"Trade megnyitva: {self.symbol} @ {self.buy_price}")

    def to_dict(self):
        """Példány szótárrá alakítása JSON mentéshez."""
        buy_time_str = self.buy_time.isoformat() if isinstance(self.buy_time, datetime) else str(self.buy_time)
        return {
            'symbol': self.symbol,
            'buy_price': self.buy_price,
            'buy_time': buy_time_str,
            'quantity': self.quantity,
        }
    @classmethod
    def close_trade(cls, symbol):
        """
        Lezár egy kereskedést a devizapár neve alapján és eltávolítja az aktív listából.
        """
        if symbol in cls.active_trades:
            del cls.active_trades[symbol]
            print(f"Trade lezárva: {symbol}")
            return True
        return False

    @classmethod
    def get_all_trades(cls):
        """Visszaadja az összes aktív trade példányt egy listában."""
        return list(cls.active_trades.values())

    @classmethod
    def get_trade_by_attribute(cls, attr, value):
        """
        Megkeres egy trade példányt egy attribútum értéke alapján.
        Visszaadja az első találatot, vagy None-t, ha nincs ilyen.
        
        Példa: Trade.get_trade_by_attribute('symbol', 'BTCUSDT')
        """
        for trade_instance in cls.active_trades.values():
            if hasattr(trade_instance, attr) and getattr(trade_instance, attr) == value:
                return trade_instance
        return None

    @classmethod
    def get_trades_as_dict(cls):
        """Visszaadja az aktív trade-eket mentésre kész szótár formátumban."""
        return {symbol: trade.to_dict() for symbol, trade in cls.active_trades.items()}


    @classmethod
    def from_dict(cls, data):
        """Létrehoz egy Trade példányt egy szótárból (pl. JSON betöltés után)."""
        buy_time = datetime.fromisoformat(data['buy_time'])

        # Itt egy trükköt alkalmazunk: ideiglenesen megnöveljük a max_trades-t,
        # hogy a __init__ ellenőrzésen átmenjen a betöltés.
        original_max = cls.max_trades
        cls.max_trades += 1
        try:
            instance = cls(symbol=data['symbol'], buy_price=data['buy_price'], buy_time=buy_time, quantity=data['quantity'])
        finally:
            # Visszaállítjuk az eredeti limitet, akár sikeres volt a betöltés, akár nem.
            cls.max_trades = original_max

        # A __init__ már hozzáadta a példányt az active_trades-hez, így csak visszaadjuk.
        return instance

    @classmethod
    def load_from_dict(cls, trades_data):
        """Helyreállítja az active_trades állapotot egy szótárból."""
        cls.active_trades.clear()
        for symbol, trade_data in trades_data.items():
            try:
                # A from_dict metódussal hozzuk létre a példányt, ami már kezeli a __init__-et
                cls.from_dict(trade_data)
            except (KeyError, ValueError) as e:
                print(f"Hiba a(z) {symbol} trade betöltésekor a fájlból: {e}. A trade kihagyva.")
        print(f"{len(cls.active_trades)} trade sikeresen betöltve.")