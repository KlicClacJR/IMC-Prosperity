def run(self, state: TradingState):
    result: Dict[str, List[Order]] = {}
    memory = self.load_memory(state.traderData)

    positions = state.position if isinstance(state.position, dict) else {}
    order_depths = state.order_depths if isinstance(state.order_depths, dict) else {}

    for product, order_depth in order_depths.items():
        if product not in self.POSITION_LIMITS:
            continue
        if order_depth is None:
            result[product] = []
            continue

        position = int(positions.get(product, 0))

        if product == "EMERALDS":
            orders = self.trade_emeralds(order_depth, position)
        elif product == "TOMATOES":
            orders = self.trade_tomatoes(order_depth, position, memory)
        else:
            orders = []

        result[product] = orders

    try:
        trader_data = json.dumps(memory, separators=(",", ":"))
    except Exception:
        trader_data = "{\"mid_history\":{\"TOMATOES\":[]}}"

    conversions = 0
    return result, conversions, trader_data

def load_memory(self, trader_data: str):
    if trader_data:
        try:
            memory = json.loads(trader_data)
        except Exception:
            memory = {}
    else:
        memory = {}

    if not isinstance(memory, dict):
        memory = {}

    mid_history = memory.get("mid_history")
    if not isinstance(mid_history, dict):
        mid_history = {}

    tomatoes_hist = mid_history.get("TOMATOES")
    if not isinstance(tomatoes_hist, list):
        tomatoes_hist = []

    clean_hist: List[float] = []
    for v in tomatoes_hist:
        if isinstance(v, (int, float)) and math.isfinite(float(v)):
            clean_hist.append(float(v))

    mid_history["TOMATOES"] = clean_hist[-80:]
    memory["mid_history"] = mid_history
    return memory

def get_best_bid_ask(self, order_depth: OrderDepth):
    if order_depth is None:
        return None, None

    buy_orders = order_depth.buy_orders if order_depth.buy_orders else {}
    sell_orders = order_depth.sell_orders if order_depth.sell_orders else {}

    best_bid = max(buy_orders.keys()) if buy_orders else None
    best_ask = min(sell_orders.keys()) if sell_orders else None
    return best_bid, best_ask

def get_mid_price(self, order_depth: OrderDepth):
    best_bid, best_ask = self.get_best_bid_ask(order_depth)
    if best_bid is not None and best_ask is not None:
        return (best_bid + best_ask) / 2
    elif best_bid is not None:
        return float(best_bid)
    elif best_ask is not None:
        return float(best_ask)
    return None

def get_microprice(self, order_depth: OrderDepth):
    best_bid, best_ask = self.get_best_bid_ask(order_depth)
    if best_bid is None or best_ask is None:
        return self.get_mid_price(order_depth)

    bid_vol = abs(order_depth.buy_orders.get(best_bid, 0))
    ask_vol = abs(order_depth.sell_orders.get(best_ask, 0))

    if bid_vol + ask_vol == 0:
        return (best_bid + best_ask) / 2

    micro = (best_ask * bid_vol + best_bid * ask_vol) / (bid_vol + ask_vol)
    return micro

def get_imbalance(self, order_depth: OrderDepth):
    best_bid, best_ask = self.get_best_bid_ask(order_depth)
    if best_bid is None or best_ask is None:
        return 0.0

    bid_vol = abs(order_depth.buy_orders.get(best_bid, 0))
    ask_vol = abs(order_depth.sell_orders.get(best_ask, 0))
    denom = bid_vol + ask_vol
    if denom == 0:
        return 0.0

    return (bid_vol - ask_vol) / denom

def clamp_qty(self, product: str, position: int, qty: int) -> int:
    pos_limit = self.POSITION_LIMITS[product]
    if qty > 0:
        return max(0, min(int(qty), pos_limit - position))
    if qty < 0:
        return min(0, max(int(qty), -pos_limit - position))
    return 0

def take_liquidity(
    self,
    product: str,
    order_depth: OrderDepth,
    fair_value: float,
    position: int,
    buy_take_threshold: float,
    sell_take_threshold: float,
) -> List[Order]:
    orders: List[Order] = []

    if order_depth is None:
        return orders

    if order_depth.sell_orders:
        for ask in sorted(order_depth.sell_orders.keys()):
            ask_volume = abs(order_depth.sell_orders[ask])
            if ask_volume <= 0:
                continue

            if ask >= fair_value - buy_take_threshold:
                break

            buy_qty = self.clamp_qty(product, position, min(ask_volume, self.POSITION_LIMITS[product]))
            if buy_qty > 0:
                orders.append(Order(product, ask, buy_qty))
                position += buy_qty

    if order_depth.buy_orders:
        for bid in sorted(order_depth.buy_orders.keys(), reverse=True):
            bid_volume = abs(order_depth.buy_orders[bid])
            if bid_volume <= 0:
                continue

            if bid <= fair_value + sell_take_threshold:
                break

            sell_qty = self.clamp_qty(product, position, -min(bid_volume, self.POSITION_LIMITS[product]))
            if sell_qty < 0:
                orders.append(Order(product, bid, sell_qty))
                position += sell_qty

    return orders

def make_market(
    self,
    product: str,
    order_depth: OrderDepth,
    fair_value: float,
    position: int,
    base_half_spread: int,
    inventory_skew: float,
    base_size: int,
) -> List[Order]:
    orders: List[Order] = []
    pos_limit = self.POSITION_LIMITS[product]

    best_bid, best_ask = self.get_best_bid_ask(order_depth)
    if best_bid is None or best_ask is None:
        return orders

    inv_ratio = position / pos_limit if pos_limit > 0 else 0.0
    skew = inventory_skew * position

    bid_quote = round(fair_value - base_half_spread - skew)
    ask_quote = round(fair_value + base_half_spread - skew)

    bid_quote = min(bid_quote, best_bid + 1)
    ask_quote = max(ask_quote, best_ask - 1)

    if bid_quote >= ask_quote:
        center = round(fair_value - skew)
        bid_quote = center - 1
        ask_quote = center + 1

    buy_capacity = max(0, pos_limit - position)
    sell_capacity = max(0, pos_limit + position)

    # Mild inventory-aware sizing: keep original behavior but reduce size near limits.
    size_scale = max(2, base_size - abs(position) // 5)
    if abs(inv_ratio) > 0.7:
        size_scale = max(2, size_scale - 1)

    # If inventory is heavy on one side, slightly prefer reducing it.
    place_buy = buy_capacity > 0
    place_sell = sell_capacity > 0
    if position >= int(0.85 * pos_limit):
        place_buy = False
    elif position <= -int(0.85 * pos_limit):
        place_sell = False

    if place_buy:
        buy_qty = min(size_scale, buy_capacity)
        buy_qty = self.clamp_qty(product, position, buy_qty)
        if buy_qty > 0:
            orders.append(Order(product, bid_quote, buy_qty))
            position += buy_qty

    if place_sell:
        sell_qty = min(size_scale, sell_capacity)
        sell_qty = self.clamp_qty(product, position, -sell_qty)
        if sell_qty < 0:
            orders.append(Order(product, ask_quote, sell_qty))
            position += sell_qty

    return orders

def trade_emeralds(self, order_depth: OrderDepth, position: int) -> List[Order]:
    mid = self.get_mid_price(order_depth)
    if mid is None:
        return []

    micro = self.get_microprice(order_depth)
    if micro is None:
        micro = mid

    imbalance = self.get_imbalance(order_depth)

    # Keep original signal, add a light anchor to near-fixed fair value 10000.
    signal_fair = (
        10000.0
        - 0.19914599256306142 * (micro - mid)
        + 0.2830318695336046 * imbalance
    )
    fair_value = 0.85 * signal_fair + 0.15 * 10000.0

    inv_ratio = position / self.POSITION_LIMITS["EMERALDS"]

    # Aggressive taking only when edge is clearly strong.
    base_take = 0.8249847899053748
    buy_take_threshold = base_take + 0.35 * max(0.0, inv_ratio)
    sell_take_threshold = base_take + 0.35 * max(0.0, -inv_ratio)

    orders: List[Order] = []
    orders += self.take_liquidity(
        product="EMERALDS",
        order_depth=order_depth,
        fair_value=fair_value,
        position=position,
        buy_take_threshold=buy_take_threshold,
        sell_take_threshold=sell_take_threshold,
    )

    net_after = position + sum(o.quantity for o in orders)

    orders += self.make_market(
        product="EMERALDS",
        order_depth=order_depth,
        fair_value=fair_value,
        position=net_after,
        base_half_spread=3,
        inventory_skew=0.122,  # mild increase vs original
        base_size=7,
    )

    return orders

def trade_tomatoes(self, order_depth: OrderDepth, position: int, memory) -> List[Order]:
    orders: List[Order] = []

    mid = self.get_mid_price(order_depth)
    if mid is None:
        return orders

    micro = self.get_microprice(order_depth)
    if micro is None:
        micro = mid

    imbalance = self.get_imbalance(order_depth)

    hist = memory["mid_history"]["TOMATOES"]
    hist.append(float(mid))
    if len(hist) > 80:
        del hist[:-80]

    short_mean = sum(hist[-6:]) / min(len(hist), 6)
    long_mean = sum(hist[-38:]) / min(len(hist), 38)

    last_mid = hist[-2] if len(hist) >= 2 else mid
    prev_mid = hist[-3] if len(hist) >= 3 else last_mid

    mom1 = mid - last_mid
    mom2 = last_mid - prev_mid

    # Preserve original profitable TOMATOES fair value logic.
    fair_value = (
        0.4027408551408521 * short_mean
        + 0.5972591448591479 * long_mean
        + 0.7199377953272201 * (micro - mid)
        + 0.9122593492936208 * imbalance
        - 0.42064762831274155 * mom1
        - 0.3172550293009319 * mom2
    )

    inv_ratio = position / self.POSITION_LIMITS["TOMATOES"]

    # Small inventory-aware threshold adjustment to avoid sticking at extremes.
    base_take = 1.0538608108667
    buy_take_threshold = base_take + 0.22 * max(0.0, inv_ratio)
    sell_take_threshold = base_take + 0.22 * max(0.0, -inv_ratio)

    orders += self.take_liquidity(
        product="TOMATOES",
        order_depth=order_depth,
        fair_value=fair_value,
        position=position,
        buy_take_threshold=buy_take_threshold,
        sell_take_threshold=sell_take_threshold,
    )

    net_after = position + sum(o.quantity for o in orders)

    orders += self.make_market(
        product="TOMATOES",
        order_depth=order_depth,
        fair_value=fair_value,
        position=net_after,
        base_half_spread=3,
        inventory_skew=0.090,  # mild increase vs original
        base_size=5,
    )

    return orders
