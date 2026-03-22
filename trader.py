from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import json


class Trader:
    POSITION_LIMITS = {
        "EMERALDS": 20,
        "TOMATOES": 20,
    }

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        memory = self.load_memory(state.traderData)

        for product in state.order_depths:
            if product not in self.POSITION_LIMITS:
                continue

            order_depth = state.order_depths[product]
            position = state.position.get(product, 0)

            if product == "EMERALDS":
                orders = self.trade_emeralds(order_depth, position)
            elif product == "TOMATOES":
                orders = self.trade_tomatoes(order_depth, position, memory)
            else:
                orders = []

            result[product] = orders

        trader_data = json.dumps(memory)
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

        if "mid_history" not in memory:
            memory["mid_history"] = {"TOMATOES": []}
        if "TOMATOES" not in memory["mid_history"]:
            memory["mid_history"]["TOMATOES"] = []

        return memory

    def get_best_bid_ask(self, order_depth: OrderDepth):
        best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
        best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None
        return best_bid, best_ask

    def get_mid_price(self, order_depth: OrderDepth):
        best_bid, best_ask = self.get_best_bid_ask(order_depth)
        if best_bid is not None and best_ask is not None:
            return (best_bid + best_ask) / 2
        elif best_bid is not None:
            return best_bid
        elif best_ask is not None:
            return best_ask
        return None

    def get_microprice(self, order_depth: OrderDepth):
        best_bid, best_ask = self.get_best_bid_ask(order_depth)
        if best_bid is None or best_ask is None:
            return self.get_mid_price(order_depth)

        bid_vol = abs(order_depth.buy_orders[best_bid])
        ask_vol = abs(order_depth.sell_orders[best_ask])

        if bid_vol + ask_vol == 0:
            return (best_bid + best_ask) / 2

        micro = (best_ask * bid_vol + best_bid * ask_vol) / (bid_vol + ask_vol)
        return micro

    def get_imbalance(self, order_depth: OrderDepth):
        best_bid, best_ask = self.get_best_bid_ask(order_depth)
        if best_bid is None or best_ask is None:
            return 0.0

        bid_vol = abs(order_depth.buy_orders[best_bid])
        ask_vol = abs(order_depth.sell_orders[best_ask])
        denom = bid_vol + ask_vol
        if denom == 0:
            return 0.0

        return (bid_vol - ask_vol) / denom

    def take_liquidity(
        self,
        product: str,
        order_depth: OrderDepth,
        fair_value: float,
        position: int,
        take_threshold: float,
    ) -> List[Order]:
        orders: List[Order] = []
        pos_limit = self.POSITION_LIMITS[product]

        if order_depth.sell_orders:
            for ask in sorted(order_depth.sell_orders.keys()):
                ask_volume = -order_depth.sell_orders[ask]
                if ask < fair_value - take_threshold:
                    buy_qty = min(ask_volume, pos_limit - position)
                    if buy_qty > 0:
                        orders.append(Order(product, ask, buy_qty))
                        position += buy_qty

        if order_depth.buy_orders:
            for bid in sorted(order_depth.buy_orders.keys(), reverse=True):
                bid_volume = order_depth.buy_orders[bid]
                if bid > fair_value + take_threshold:
                    sell_qty = min(bid_volume, pos_limit + position)
                    if sell_qty > 0:
                        orders.append(Order(product, bid, -sell_qty))
                        position -= sell_qty

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

        skew = inventory_skew * position
        bid_quote = round(fair_value - base_half_spread - skew)
        ask_quote = round(fair_value + base_half_spread - skew)

        bid_quote = min(bid_quote, best_bid + 1)
        ask_quote = max(ask_quote, best_ask - 1)

        if bid_quote >= ask_quote:
            bid_quote = ask_quote - 1

        buy_capacity = pos_limit - position
        sell_capacity = pos_limit + position

        size_scale = max(2, base_size - abs(position) // 5)

        if buy_capacity > 0:
            orders.append(Order(product, bid_quote, min(size_scale, buy_capacity)))
        if sell_capacity > 0:
            orders.append(Order(product, ask_quote, -min(size_scale, sell_capacity)))

        return orders

    def trade_emeralds(self, order_depth: OrderDepth, position: int) -> List[Order]:
        mid = self.get_mid_price(order_depth)
        if mid is None:
            return []
        micro = self.get_microprice(order_depth)
        imbalance = self.get_imbalance(order_depth)
        fair_value = (
            10000.0
            - 0.19914599256306142 * (micro - mid)
            + 0.2830318695336046 * imbalance
        )

        orders: List[Order] = []
        orders += self.take_liquidity(
            product="EMERALDS",
            order_depth=order_depth,
            fair_value=fair_value,
            position=position,
            take_threshold=0.8249847899053748,
        )

        net_after = position + sum(o.quantity for o in orders)

        orders += self.make_market(
            product="EMERALDS",
            order_depth=order_depth,
            fair_value=fair_value,
            position=net_after,
            base_half_spread=3,
            inventory_skew=0.11650582963402509,
            base_size=7,
        )

        return orders

    def trade_tomatoes(self, order_depth: OrderDepth, position: int, memory) -> List[Order]:
        orders: List[Order] = []

        mid = self.get_mid_price(order_depth)
        if mid is None:
            return orders

        micro = self.get_microprice(order_depth)
        imbalance = self.get_imbalance(order_depth)

        hist = memory["mid_history"]["TOMATOES"]
        hist.append(mid)
        if len(hist) > 80:
            hist.pop(0)

        short_mean = sum(hist[-6:]) / min(len(hist), 6)
        long_mean = sum(hist[-38:]) / min(len(hist), 38)

        last_mid = hist[-2] if len(hist) >= 2 else mid
        prev_mid = hist[-3] if len(hist) >= 3 else last_mid

        mom1 = mid - last_mid
        mom2 = last_mid - prev_mid

        fair_value = (
            0.4027408551408521 * short_mean
            + 0.5972591448591479 * long_mean
            + 0.7199377953272201 * (micro - mid)
            + 0.9122593492936208 * imbalance
            - 0.42064762831274155 * mom1
            - 0.3172550293009319 * mom2
        )

        orders += self.take_liquidity(
            product="TOMATOES",
            order_depth=order_depth,
            fair_value=fair_value,
            position=position,
            take_threshold=1.0538608108667,
        )

        net_after = position + sum(o.quantity for o in orders)

        orders += self.make_market(
            product="TOMATOES",
            order_depth=order_depth,
            fair_value=fair_value,
            position=net_after,
            base_half_spread=3,
            inventory_skew=0.086677641005485,
            base_size=5,
        )

        return orders
