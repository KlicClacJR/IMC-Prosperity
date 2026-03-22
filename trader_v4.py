from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Tuple
import json
import math


class Trader:
    POSITION_LIMITS = {
        "EMERALDS": 20,
        "TOMATOES": 20,
    }

    TOMATO_HISTORY_LIMIT = 100

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

        cleaned_hist: List[float] = []
        for v in tomatoes_hist:
            if isinstance(v, (int, float)) and math.isfinite(float(v)):
                cleaned_hist.append(float(v))

        mid_history["TOMATOES"] = cleaned_hist[-self.TOMATO_HISTORY_LIMIT :]
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
        if best_bid is not None:
            return float(best_bid)
        if best_ask is not None:
            return float(best_ask)
        return None

    def get_spread(self, order_depth: OrderDepth, default_spread: float = 2.0) -> float:
        best_bid, best_ask = self.get_best_bid_ask(order_depth)
        if best_bid is None or best_ask is None:
            return default_spread
        return max(1.0, float(best_ask - best_bid))

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

    def clamp_qty(self, product: str, position: int, desired_qty: int) -> int:
        limit = self.POSITION_LIMITS[product]
        if desired_qty > 0:
            return max(0, min(int(desired_qty), limit - position))
        if desired_qty < 0:
            return min(0, max(int(desired_qty), -limit - position))
        return 0

    def take_liquidity(
        self,
        product: str,
        order_depth: OrderDepth,
        fair_value: float,
        position: int,
        buy_take_threshold: float,
        sell_take_threshold: float,
        max_buy_take: int,
        max_sell_take: int,
    ) -> Tuple[List[Order], int]:
        orders: List[Order] = []
        projected_pos = position

        if order_depth is None:
            return orders, projected_pos

        bought = 0
        if max_buy_take > 0 and order_depth.sell_orders:
            for ask in sorted(order_depth.sell_orders.keys()):
                ask_volume = abs(order_depth.sell_orders[ask])
                if ask_volume <= 0:
                    continue

                edge = fair_value - ask
                if edge < buy_take_threshold:
                    break

                remaining = max_buy_take - bought
                if remaining <= 0:
                    break

                desired_qty = min(ask_volume, remaining)
                qty = self.clamp_qty(product, projected_pos, desired_qty)
                if qty <= 0:
                    break

                orders.append(Order(product, ask, qty))
                projected_pos += qty
                bought += qty

        sold = 0
        if max_sell_take > 0 and order_depth.buy_orders:
            for bid in sorted(order_depth.buy_orders.keys(), reverse=True):
                bid_volume = abs(order_depth.buy_orders[bid])
                if bid_volume <= 0:
                    continue

                edge = bid - fair_value
                if edge < sell_take_threshold:
                    break

                remaining = max_sell_take - sold
                if remaining <= 0:
                    break

                desired_qty = -min(bid_volume, remaining)
                qty = self.clamp_qty(product, projected_pos, desired_qty)
                if qty >= 0:
                    break

                orders.append(Order(product, bid, qty))
                projected_pos += qty
                sold += -qty

        return orders, projected_pos

    def make_market(
        self,
        product: str,
        order_depth: OrderDepth,
        fair_value: float,
        position: int,
        base_half_spread: float,
        inventory_skew: float,
        base_size: int,
        edge_strength: float,
        min_edge: float,
        soft_limit_ratio: float,
    ) -> List[Order]:
        orders: List[Order] = []
        pos_limit = self.POSITION_LIMITS[product]

        if order_depth is None:
            return orders

        best_bid, best_ask = self.get_best_bid_ask(order_depth)
        if best_bid is None and best_ask is None:
            return orders

        spread = self.get_spread(order_depth)
        inv_ratio_signed = position / pos_limit if pos_limit > 0 else 0.0
        inv_ratio_abs = abs(inv_ratio_signed)

        conviction = edge_strength / max(1.0, spread)

        reservation = fair_value - inventory_skew * position
        half_spread = base_half_spread + 1.15 * inv_ratio_abs - 0.55 * min(conviction, 1.8)
        half_spread = max(1.0, half_spread)

        bid_quote = int(round(reservation - half_spread))
        ask_quote = int(round(reservation + half_spread))

        if best_bid is not None:
            bid_quote = min(bid_quote, best_bid + 1)
        if best_ask is not None:
            ask_quote = max(ask_quote, best_ask - 1)

        if bid_quote >= ask_quote:
            center = int(round(reservation))
            bid_quote = center - 1
            ask_quote = center + 1

        direction_mid = self.get_mid_price(order_depth)
        direction = fair_value - (direction_mid if direction_mid is not None else fair_value)
        tilt_ticks = int(min(2, max(0, round(abs(direction) / max(1.0, spread)))))
        if direction > 0:
            ask_quote += tilt_ticks
        elif direction < 0:
            bid_quote -= tilt_ticks

        if bid_quote >= ask_quote:
            center = int(round(reservation))
            bid_quote = center - 1
            ask_quote = center + 1

        size_core = base_size * (1.0 + 0.35 * min(conviction, 2.0)) * max(0.25, 1.0 - 0.85 * inv_ratio_abs)
        size_core_int = max(1, int(round(size_core)))

        buy_bias = 1.0 - 0.95 * max(0.0, inv_ratio_signed) + 0.30 * max(0.0, -inv_ratio_signed)
        sell_bias = 1.0 - 0.95 * max(0.0, -inv_ratio_signed) + 0.30 * max(0.0, inv_ratio_signed)

        buy_size = max(1, int(round(size_core_int * max(0.15, buy_bias))))
        sell_size = max(1, int(round(size_core_int * max(0.15, sell_bias))))

        soft_limit = int(round(pos_limit * soft_limit_ratio))

        buy_capacity = max(0, pos_limit - position)
        sell_capacity = max(0, pos_limit + position)

        buy_edge = fair_value - bid_quote
        sell_edge = ask_quote - fair_value

        place_buy = buy_capacity > 0 and buy_edge >= min_edge
        place_sell = sell_capacity > 0 and sell_edge >= min_edge

        if position >= soft_limit:
            place_buy = False
            place_sell = sell_capacity > 0
        elif position <= -soft_limit:
            place_sell = False
            place_buy = buy_capacity > 0

        if place_buy:
            qty = min(buy_size, buy_capacity)
            if qty > 0:
                orders.append(Order(product, bid_quote, qty))

        if place_sell:
            qty = min(sell_size, sell_capacity)
            if qty > 0:
                orders.append(Order(product, ask_quote, -qty))

        return orders

    def trade_emeralds(self, order_depth: OrderDepth, position: int) -> List[Order]:
        mid = self.get_mid_price(order_depth)
        micro = self.get_microprice(order_depth)
        imbalance = self.get_imbalance(order_depth)

        mid_ref = mid if mid is not None else 10000.0
        micro_ref = micro if micro is not None else mid_ref

        signal_fair = (
            10000.0
            - 0.19914599256306142 * (micro_ref - mid_ref)
            + 0.2830318695336046 * imbalance
        )

        fair_value = 0.75 * 10000.0 + 0.25 * signal_fair

        pos_limit = self.POSITION_LIMITS["EMERALDS"]
        inv_ratio = position / pos_limit if pos_limit > 0 else 0.0

        spread = self.get_spread(order_depth)
        edge_strength = abs((mid_ref if mid is not None else fair_value) - fair_value)
        conviction = edge_strength / max(1.0, spread)

        base_take = max(0.45, 0.8249847899053748 - 0.18 * min(conviction, 1.8))
        buy_take_threshold = base_take + 0.95 * max(0.0, inv_ratio)
        sell_take_threshold = base_take + 0.95 * max(0.0, -inv_ratio)

        soft_limit = int(round(0.75 * pos_limit))
        max_buy_take = max(0, int(round(8 * max(0.2, 1.0 - max(0.0, inv_ratio)))))
        max_sell_take = max(0, int(round(8 * max(0.2, 1.0 - max(0.0, -inv_ratio)))))

        if position >= soft_limit:
            max_buy_take = 0
        if position <= -soft_limit:
            max_sell_take = 0

        orders, net_after = self.take_liquidity(
            product="EMERALDS",
            order_depth=order_depth,
            fair_value=fair_value,
            position=position,
            buy_take_threshold=buy_take_threshold,
            sell_take_threshold=sell_take_threshold,
            max_buy_take=max_buy_take,
            max_sell_take=max_sell_take,
        )

        edge_after_take = abs((mid_ref if mid is not None else fair_value) - fair_value)
        orders += self.make_market(
            product="EMERALDS",
            order_depth=order_depth,
            fair_value=fair_value,
            position=net_after,
            base_half_spread=1.8,
            inventory_skew=0.155,
            base_size=8,
            edge_strength=edge_after_take,
            min_edge=0.30,
            soft_limit_ratio=0.75,
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
        if len(hist) > self.TOMATO_HISTORY_LIMIT:
            del hist[:-self.TOMATO_HISTORY_LIMIT]

        short_mean = sum(hist[-6:]) / min(len(hist), 6)
        long_mean = sum(hist[-38:]) / min(len(hist), 38)

        last_mid = hist[-2] if len(hist) >= 2 else mid
        prev_mid = hist[-3] if len(hist) >= 3 else last_mid

        mom1 = mid - last_mid
        mom2 = last_mid - prev_mid

        fair_existing = (
            0.4027408551408521 * short_mean
            + 0.5972591448591479 * long_mean
            + 0.7199377953272201 * (micro - mid)
            + 0.9122593492936208 * imbalance
            - 0.42064762831274155 * mom1
            - 0.3172550293009319 * mom2
        )

        short_reversion = short_mean - mid
        long_reversion = long_mean - mid
        drift = short_mean - long_mean

        fair_enhanced = (
            0.20 * mid
            + 0.34 * short_mean
            + 0.46 * long_mean
            + 0.62 * (micro - mid)
            + 0.88 * imbalance
            + 0.30 * short_reversion
            + 0.14 * long_reversion
            + 0.10 * drift
            - 0.08 * mom1
        )

        fair_value = 0.76 * fair_existing + 0.24 * fair_enhanced

        spread = self.get_spread(order_depth)
        max_deviation = max(4.0, 2.0 * spread + 1.0)
        fair_value = mid + max(-max_deviation, min(max_deviation, fair_value - mid))

        pos_limit = self.POSITION_LIMITS["TOMATOES"]
        inv_ratio = position / pos_limit if pos_limit > 0 else 0.0

        edge_strength = abs(fair_value - mid)
        conviction = edge_strength / max(1.0, spread)

        base_take = max(0.65, 1.0538608108667 - 0.25 * min(conviction, 1.6))
        buy_take_threshold = base_take + 0.80 * max(0.0, inv_ratio)
        sell_take_threshold = base_take + 0.80 * max(0.0, -inv_ratio)

        soft_limit = int(round(0.75 * pos_limit))

        base_take_cap = 4 + int(min(6, math.floor(2.2 * conviction)))
        max_buy_take = max(0, int(round(base_take_cap * max(0.2, 1.0 - max(0.0, inv_ratio)))))
        max_sell_take = max(0, int(round(base_take_cap * max(0.2, 1.0 - max(0.0, -inv_ratio)))))

        if position >= soft_limit:
            max_buy_take = 0
        if position <= -soft_limit:
            max_sell_take = 0

        take_orders, net_after = self.take_liquidity(
            product="TOMATOES",
            order_depth=order_depth,
            fair_value=fair_value,
            position=position,
            buy_take_threshold=buy_take_threshold,
            sell_take_threshold=sell_take_threshold,
            max_buy_take=max_buy_take,
            max_sell_take=max_sell_take,
        )
        orders += take_orders

        edge_after_take = abs(fair_value - mid)
        orders += self.make_market(
            product="TOMATOES",
            order_depth=order_depth,
            fair_value=fair_value,
            position=net_after,
            base_half_spread=2.9,
            inventory_skew=0.105,
            base_size=5,
            edge_strength=edge_after_take,
            min_edge=0.35,
            soft_limit_ratio=0.75,
        )

        return orders
