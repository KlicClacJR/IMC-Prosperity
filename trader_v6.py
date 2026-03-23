from datamodel import OrderDepth, TradingState, Order


class Trader:
    POSITION_LIMITS = {
        "EMERALDS": 20,
        "TOMATOES": 20,
    }

    EMERALDS_FAIR = 10000.0
    HISTORY_MAX = 90

    def run(self, state: TradingState):
        result = {}
        memory = self._load_memory(getattr(state, "traderData", ""))

        order_depths = getattr(state, "order_depths", {}) or {}
        positions = getattr(state, "position", {}) or {}

        for product in self.POSITION_LIMITS:
            depth = order_depths.get(product)
            if depth is None:
                continue

            position = positions.get(product, 0)

            if product == "EMERALDS":
                orders = self._trade_emeralds(depth, position)
            else:
                orders = self._trade_tomatoes(depth, position, memory)

            result[product] = orders

        trader_data = self._dump_memory(memory)
        conversions = 0
        return result, conversions, trader_data

    def _load_memory(self, trader_data: str):
        history = []
        if trader_data:
            for token in trader_data.split(","):
                if not token:
                    continue
                try:
                    history.append(float(token))
                except Exception:
                    continue

        if len(history) > self.HISTORY_MAX:
            history = history[-self.HISTORY_MAX :]

        return {"t": history}

    def _dump_memory(self, memory):
        history = memory.get("t", [])
        if not isinstance(history, list):
            history = []

        out = []
        for value in history[-self.HISTORY_MAX :]:
            try:
                out.append(str(round(float(value), 3)))
            except Exception:
                continue
        return ",".join(out)

    def _best_bid_ask(self, order_depth: OrderDepth):
        buy_orders = getattr(order_depth, "buy_orders", {}) or {}
        sell_orders = getattr(order_depth, "sell_orders", {}) or {}

        best_bid = max(buy_orders.keys()) if buy_orders else None
        best_ask = min(sell_orders.keys()) if sell_orders else None
        return best_bid, best_ask

    def _mid_price(self, order_depth: OrderDepth):
        best_bid, best_ask = self._best_bid_ask(order_depth)
        if best_bid is not None and best_ask is not None:
            return (best_bid + best_ask) / 2.0
        if best_bid is not None:
            return float(best_bid)
        if best_ask is not None:
            return float(best_ask)
        return None

    def _microprice(self, order_depth: OrderDepth):
        buy_orders = getattr(order_depth, "buy_orders", {}) or {}
        sell_orders = getattr(order_depth, "sell_orders", {}) or {}
        best_bid, best_ask = self._best_bid_ask(order_depth)

        if best_bid is None or best_ask is None:
            return self._mid_price(order_depth)

        bid_vol = buy_orders.get(best_bid, 0)
        ask_vol = -sell_orders.get(best_ask, 0)

        if bid_vol < 0:
            bid_vol = 0
        if ask_vol < 0:
            ask_vol = 0

        denom = bid_vol + ask_vol
        if denom <= 0:
            return (best_bid + best_ask) / 2.0

        return (best_ask * bid_vol + best_bid * ask_vol) / float(denom)

    def _imbalance(self, order_depth: OrderDepth):
        buy_orders = getattr(order_depth, "buy_orders", {}) or {}
        sell_orders = getattr(order_depth, "sell_orders", {}) or {}
        best_bid, best_ask = self._best_bid_ask(order_depth)

        if best_bid is None or best_ask is None:
            return 0.0

        bid_vol = buy_orders.get(best_bid, 0)
        ask_vol = -sell_orders.get(best_ask, 0)

        if bid_vol < 0:
            bid_vol = 0
        if ask_vol < 0:
            ask_vol = 0

        denom = bid_vol + ask_vol
        if denom <= 0:
            return 0.0

        return (bid_vol - ask_vol) / float(denom)

    def _avg(self, values, window, fallback):
        if not values:
            return fallback
        n = window if len(values) >= window else len(values)
        if n <= 0:
            return fallback
        return sum(values[-n:]) / float(n)

    def _edge_size(self, edge, base_clip, max_clip):
        if edge >= 8.0:
            return max_clip
        if edge >= 5.0:
            return min(max_clip, base_clip + 2)
        if edge >= 3.0:
            return min(max_clip, base_clip + 1)
        return max(1, base_clip)

    def _add_order(self, orders, product, price, qty, position, used):
        if price is None:
            return 0

        p = int(round(price))
        q = int(qty)

        if p <= 0 or q == 0:
            return 0

        limit = self.POSITION_LIMITS[product]

        if q > 0:
            cap = limit - position - used["buy"]
            if cap <= 0:
                return 0
            q = min(q, cap)
            if q <= 0:
                return 0
            used["buy"] += q
            orders.append(Order(product, p, q))
            return q

        cap = limit + position - used["sell"]
        if cap <= 0:
            return 0
        sell_qty = min(-q, cap)
        if sell_qty <= 0:
            return 0
        used["sell"] += sell_qty
        orders.append(Order(product, p, -sell_qty))
        return sell_qty

    def _trade_emeralds(self, order_depth: OrderDepth, position: int):
        product = "EMERALDS"
        orders = []
        used = {"buy": 0, "sell": 0}

        buy_orders = getattr(order_depth, "buy_orders", {}) or {}
        sell_orders = getattr(order_depth, "sell_orders", {}) or {}
        best_bid, best_ask = self._best_bid_ask(order_depth)
        mid = self._mid_price(order_depth)
        micro = self._microprice(order_depth)
        imbalance = self._imbalance(order_depth)

        reservation = self.EMERALDS_FAIR - 0.22 * position

        buy_threshold = 2.6 + (0.5 if position > 10 else 0.0) + (0.9 if position > 16 else 0.0)
        sell_threshold = 2.6 + (0.5 if position < -10 else 0.0) + (0.9 if position < -16 else 0.0)

        buy_take_cap = 7
        sell_take_cap = 7

        if sell_orders:
            for ask in sorted(sell_orders.keys()):
                available = -sell_orders.get(ask, 0)
                if available <= 0:
                    continue

                edge = reservation - ask
                if edge < buy_threshold:
                    break

                if used["buy"] >= buy_take_cap:
                    break

                projected = position + used["buy"] - used["sell"]
                clip = self._edge_size(edge, 1, 4)
                if projected > 8:
                    clip = max(1, clip - 1)
                if projected > 14:
                    clip = 1

                remaining_cap = buy_take_cap - used["buy"]
                desired = min(available, clip, remaining_cap)
                added = self._add_order(orders, product, ask, desired, position, used)
                if added <= 0:
                    break

        if buy_orders:
            for bid in sorted(buy_orders.keys(), reverse=True):
                available = buy_orders.get(bid, 0)
                if available <= 0:
                    continue

                edge = bid - reservation
                if edge < sell_threshold:
                    break

                if used["sell"] >= sell_take_cap:
                    break

                projected = position + used["buy"] - used["sell"]
                clip = self._edge_size(edge, 1, 4)
                if projected < -8:
                    clip = max(1, clip - 1)
                if projected < -14:
                    clip = 1

                remaining_cap = sell_take_cap - used["sell"]
                desired = min(available, clip, remaining_cap)
                added = self._add_order(orders, product, bid, -desired, position, used)
                if added <= 0:
                    break

        if best_bid is None or best_ask is None:
            return orders

        spread = best_ask - best_bid
        if spread <= 0:
            return orders

        projected = position + used["buy"] - used["sell"]
        inv_shift = int(round(0.14 * projected))

        step_bid = False
        step_ask = False
        near_fair = mid is not None and abs(mid - self.EMERALDS_FAIR) <= 2.5

        if spread >= 14:
            if abs(projected) <= 4 and near_fair:
                step_bid = imbalance > -0.25
                step_ask = imbalance < 0.25
            elif projected < 0:
                step_bid = imbalance > -0.40
            elif projected > 0:
                step_ask = imbalance < 0.40
        elif spread >= 10:
            if projected <= -10 and imbalance > -0.35:
                step_bid = True
            if projected >= 10 and imbalance < 0.35:
                step_ask = True

        if micro is not None and mid is not None:
            if micro < mid - 0.8:
                step_bid = False
            if micro > mid + 0.8:
                step_ask = False

        bid_quote = best_bid + (1 if step_bid else 0)
        ask_quote = best_ask - (1 if step_ask else 0)

        bid_quote -= inv_shift
        ask_quote -= inv_shift

        bid_quote = min(bid_quote, int(self.EMERALDS_FAIR + 2))
        ask_quote = max(ask_quote, int(self.EMERALDS_FAIR - 2))

        bid_quote = min(bid_quote, best_ask - 1)
        ask_quote = max(ask_quote, best_bid + 1)

        if bid_quote >= ask_quote:
            center = int(round((best_bid + best_ask) / 2.0))
            bid_quote = min(center - 1, best_ask - 1)
            ask_quote = max(center + 1, best_bid + 1)

        if bid_quote >= ask_quote:
            return orders

        if spread <= 2:
            base_size = 1
        elif spread <= 6:
            base_size = 2
        else:
            base_size = 3

        buy_size = base_size
        sell_size = base_size

        if abs(projected) >= 12:
            buy_size = max(1, buy_size - 1)
            sell_size = max(1, sell_size - 1)

        if projected >= 14:
            buy_size = 0
            sell_size = max(sell_size, 5)
        elif projected >= 9:
            buy_size = 1
            sell_size = max(sell_size, 4)
        elif projected <= -14:
            sell_size = 0
            buy_size = max(buy_size, 5)
        elif projected <= -9:
            sell_size = 1
            buy_size = max(buy_size, 4)

        if buy_size > 0:
            self._add_order(orders, product, bid_quote, buy_size, position, used)
        if sell_size > 0:
            self._add_order(orders, product, ask_quote, -sell_size, position, used)

        return orders

    def _trade_tomatoes(self, order_depth: OrderDepth, position: int, memory):
        product = "TOMATOES"
        orders = []
        used = {"buy": 0, "sell": 0}

        buy_orders = getattr(order_depth, "buy_orders", {}) or {}
        sell_orders = getattr(order_depth, "sell_orders", {}) or {}
        best_bid, best_ask = self._best_bid_ask(order_depth)

        mid = self._mid_price(order_depth)
        if mid is None:
            return orders

        history = memory.get("t", [])
        if not isinstance(history, list):
            history = []

        history.append(mid)
        if len(history) > self.HISTORY_MAX:
            history.pop(0)
        memory["t"] = history

        short_ma = self._avg(history, 6, mid)
        long_ma = self._avg(history, 34, mid)

        last_mid = history[-2] if len(history) >= 2 else mid
        prev_mid = history[-3] if len(history) >= 3 else last_mid
        ret_1 = mid - last_mid
        ret_2 = last_mid - prev_mid

        micro = self._microprice(order_depth)
        if micro is None:
            micro = mid
        imbalance = self._imbalance(order_depth)

        fair_value = (
            0.42 * short_ma
            + 0.58 * long_ma
            - 0.52 * ret_1
            - 0.28 * ret_2
            + 0.10 * (micro - mid)
            + 0.16 * imbalance
        )

        reservation = fair_value - 0.16 * position

        buy_threshold = 1.35 + (0.25 if position > 8 else 0.0) + (0.45 if position > 14 else 0.0)
        sell_threshold = 1.35 + (0.25 if position < -8 else 0.0) + (0.45 if position < -14 else 0.0)

        buy_take_cap = 6
        sell_take_cap = 6

        if sell_orders:
            for ask in sorted(sell_orders.keys()):
                available = -sell_orders.get(ask, 0)
                if available <= 0:
                    continue

                edge = reservation - ask
                if edge < buy_threshold:
                    break

                if used["buy"] >= buy_take_cap:
                    break

                projected = position + used["buy"] - used["sell"]
                clip = self._edge_size(edge, 1, 4)
                if projected > 8:
                    clip = max(1, clip - 1)
                if projected > 14:
                    clip = 1

                remaining_cap = buy_take_cap - used["buy"]
                desired = min(available, clip, remaining_cap)
                added = self._add_order(orders, product, ask, desired, position, used)
                if added <= 0:
                    break

        if buy_orders:
            for bid in sorted(buy_orders.keys(), reverse=True):
                available = buy_orders.get(bid, 0)
                if available <= 0:
                    continue

                edge = bid - reservation
                if edge < sell_threshold:
                    break

                if used["sell"] >= sell_take_cap:
                    break

                projected = position + used["buy"] - used["sell"]
                clip = self._edge_size(edge, 1, 4)
                if projected < -8:
                    clip = max(1, clip - 1)
                if projected < -14:
                    clip = 1

                remaining_cap = sell_take_cap - used["sell"]
                desired = min(available, clip, remaining_cap)
                added = self._add_order(orders, product, bid, -desired, position, used)
                if added <= 0:
                    break

        if best_bid is None or best_ask is None:
            return orders

        spread = best_ask - best_bid
        if spread <= 0:
            return orders

        projected = position + used["buy"] - used["sell"]
        reservation = fair_value - 0.16 * projected
        mid_book = (best_bid + best_ask) / 2.0
        edge_mid = reservation - mid_book

        step_bid = False
        step_ask = False

        if spread >= 5:
            if edge_mid > 1.4 and projected < 12:
                step_bid = True
            elif edge_mid < -1.4 and projected > -12:
                step_ask = True
            elif spread >= 8 and abs(edge_mid) < 0.7 and abs(projected) <= 3:
                step_bid = True
                step_ask = True

        bid_quote = best_bid + (1 if step_bid else 0)
        ask_quote = best_ask - (1 if step_ask else 0)

        if spread >= 8:
            if edge_mid > 2.8 and projected < 10:
                bid_quote += 1
            elif edge_mid < -2.8 and projected > -10:
                ask_quote -= 1

        inv_shift = int(round(0.12 * projected))
        bid_quote -= inv_shift
        ask_quote -= inv_shift

        bid_floor = int(round(reservation - 5.0))
        ask_cap = int(round(reservation + 5.0))

        bid_quote = max(bid_quote, bid_floor)
        ask_quote = min(ask_quote, ask_cap)

        bid_quote = min(bid_quote, best_ask - 1)
        ask_quote = max(ask_quote, best_bid + 1)

        if bid_quote >= ask_quote:
            if edge_mid > 0:
                ask_quote = bid_quote + 1
            elif edge_mid < 0:
                bid_quote = ask_quote - 1
            else:
                center = int(round((best_bid + best_ask) / 2.0))
                bid_quote = min(center - 1, best_ask - 1)
                ask_quote = max(center + 1, best_bid + 1)

        if bid_quote >= ask_quote:
            return orders

        if spread <= 2:
            base_size = 1
        elif spread <= 4:
            base_size = 2
        else:
            base_size = 3

        if abs(edge_mid) < 0.6 and spread <= 3:
            base_size = 1

        buy_size = base_size
        sell_size = base_size

        if edge_mid > 1.6:
            buy_size += 1
            sell_size = max(1, sell_size - 1)
        elif edge_mid < -1.6:
            sell_size += 1
            buy_size = max(1, buy_size - 1)

        if edge_mid > 2.8:
            buy_size += 1
        elif edge_mid < -2.8:
            sell_size += 1

        if projected >= 14:
            buy_size = 0
            sell_size = max(sell_size, 5)
        elif projected >= 9:
            buy_size = 1
            sell_size = max(sell_size, 4)
        elif projected <= -14:
            sell_size = 0
            buy_size = max(buy_size, 5)
        elif projected <= -9:
            sell_size = 1
            buy_size = max(buy_size, 4)

        if buy_size > 0:
            self._add_order(orders, product, bid_quote, buy_size, position, used)
        if sell_size > 0:
            self._add_order(orders, product, ask_quote, -sell_size, position, used)

        return orders
