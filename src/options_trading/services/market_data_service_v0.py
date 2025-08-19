 
# was there for constructing option chain for historical prices

try:
            # fetch contract table for expiry (manager handles validation)
            contracts_df = await self.market_data_manager.fetch_contracts_for_expiry(symbol, "NSE_EQ", expiry_date)
            if contracts_df is None or contracts_df.empty:
                raise DataQualityError(f"No contracts found for {symbol} {expiry_date}")

            # Diagnostic: contract counts by type
            try:
                counts = contracts_df["instrument_type"].value_counts().to_dict()
            except Exception:
                counts = {}
            logger.debug("Contracts fetched: total=%s, by_type=%s", len(contracts_df), counts)

            # Determine spot close near expiry date to anchor ATM strikes
            spot_interval = int(self.settings.default_spot_interval or 3)

            # Try to fetch spot via cache method if available (could be sync or async)
            spot_df = None
            try:
                cache_callable = getattr(self.market_data_manager.spot_cache, "get_timeseries", None)
                if cache_callable:
                    spot_df = await self._maybe_await(
                        cache_callable,
                        uk,
                        expiry_date,
                        self.market_data_manager.access_token,
                        spot_interval,
                        fetch_upstox_historical_data,
                        ttl=60,
                        unit="minutes",
                    )
            except Exception:
                spot_df = None

            # fallback to direct API helper
            if spot_df is None or getattr(spot_df, "empty", True):
                spot_df = await self._run_blocking(
                    fetch_upstox_historical_data,
                    self.market_data_manager.access_token,
                    uk,
                    expiry_date,
                    expiry_date,
                    str(spot_interval),
                    "minutes",
                )

            if spot_df is None or getattr(spot_df, "empty", True):
                raise DataQualityError("No spot candles available to determine ATM")

            if hasattr(spot_df, "reset_index"):
                spot_df = spot_df.reset_index()

            spot_price = float(spot_df["close"].iloc[-1])

            # Select strikes around ATM
            strikes_sorted = sorted(
                contracts_df["strike_price"].dropna().unique(),
                key=lambda x: abs(float(x) - spot_price),
            )
            selected_strikes = strikes_sorted[: max(10, strikes_range * 2)]

            # build tasks to fetch option candles concurrently with semaphore
            max_concurrency = getattr(self, "_max_concurrent_requests", 8)
            semaphore = asyncio.Semaphore(max_concurrency)

            async def fetch_option_candles_with_retries(instrument_key, interval, start, end, token, retries=2):
                last_exc = None
                for attempt in range(retries + 1):
                    try:
                        return await self._maybe_await(fetch_option_candles, instrument_key, interval, start, end, token)
                    except Exception as e:
                        last_exc = e
                        logger.debug("fetch_option_candles attempt %d failed for %s: %s", attempt + 1, instrument_key, e)
                logger.warning(
                    "fetch_option_candles ultimately failed for %s after %d attempts: %s", instrument_key, retries + 1, last_exc
                )
                return None

            async def fetch_and_build(instrument_key: str, row: pd.Series) -> Optional[OptionData]:
                async with semaphore:
                    try:
                        strike = float(row["strike_price"])
                        instr_type = row.get("instrument_type", "CE")

                        # fetch option candles (blocking function -> run in thread)
                        start = (pd.to_datetime(expiry_date) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
                        option_df = await self._maybe_await(
                            fetch_option_candles, instrument_key, self.settings.default_option_interval, start, expiry_date, self.market_data_manager.access_token
                        )
                        if option_df is None or getattr(option_df, "empty", True):
                            return None
                        if hasattr(option_df, "reset_index"):
                            option_df = option_df.reset_index()

                        last_opt = option_df.iloc[-1]
                        opt_ts = pd.to_datetime(last_opt.get("timestamp", datetime.utcnow()))
                        opt_close_raw = float(last_opt.get("close", last_opt.get("close_option", last_opt.get("last", 0.0))))
                        opt_close=to_decimal_safe_np(opt_close_raw)
                        if opt_close is None:
                            logger.debug("Skipping %s: last price not finite (%s)", instrument_key, opt_close_raw)
                            return None
                        # align spot close (best effort)
                        spot_close = spot_price
                        try:
                            if "timestamp" in spot_df.columns:
                                nearest_spot = spot_df[spot_df["timestamp"] <= opt_ts]
                                if not nearest_spot.empty:
                                    spot_close = float(nearest_spot["close"].iloc[-1])
                        except Exception:
                            pass

                        one = pd.DataFrame({"timestamp": [opt_ts], "close_option": [opt_close], "close_spot": [spot_close]})

                        # compute iv + greeks in thread (these are synchronous CPU functions)
                        one = await self._run_blocking(compute_iv_in_memory, one, strike, pd.to_datetime(expiry_date), instr_type)
                        one = await self._run_blocking(append_greeks_in_memory, one, strike, pd.to_datetime(expiry_date), instr_type)

                        if not one.empty:
                            iv_val = float(one.iloc[0].get("iv", None))
                            delta_val = float(one.iloc[0].get("delta", None))
                            gamma_val = float(one.iloc[0].get("gamma", None))
                            theta_val = float(one.iloc[0].get("theta", None))
                            vega_val = float(one.iloc[0].get("vega", None))
                            rho_val = float(one.iloc[0].get("rho", None))
                        else:
                            iv_val = delta_val = gamma_val = theta_val = vega_val = rho_val = 0.0
                        # sanitize numeric values -> Decimal or None
                        iv_dec = to_decimal_safe_np(iv_val)
                        delta_dec = to_decimal_safe_np(delta_val)
                        gamma_dec = to_decimal_safe_np(gamma_val)
                        theta_dec = to_decimal_safe_np(theta_val)
                        vega_dec = to_decimal_safe_np(vega_val)
                        rho_dec = to_decimal_safe_np(rho_val)
                        last_price_dec = Decimal(str(opt_close))
                        bid = (last_price_dec * Decimal("0.995")).quantize(Decimal("0.01"))
                        ask = (last_price_dec * Decimal("1.005")).quantize(Decimal("0.01"))

                        vol = int(last_opt.get("volume", last_opt.get("vol", 0) or 0))
                        oi = int(last_opt.get("open_interest", last_opt.get("oi", 0) or 0))

                        return OptionData(
                            strike=Decimal(str(strike)),
                            option_type=instr_type,
                            last_price=last_price_dec,
                            bid=bid,
                            ask=ask,
                            volume=vol,
                            open_interest=oi,
                            implied_volatility=iv_dec,
                            greeks=GreeksSnapshot(
                                delta=delta_dec,
                                gamma=gamma_dec,
                                theta=theta_dec,
                                vega=vega_dec,
                                rho=rho_dec,
                            ),
                            last_updated=datetime.utcnow(),
                        )
                    
                    except Exception as e:
                        logger.debug("Skipping contract %s due to error: %s", instrument_key, e, exc_info=True)
                        return None

            # gather tasks for selected strikes only
            tasks = []
            for strike in selected_strikes:
                subset = contracts_df[contracts_df["strike_price"] == strike]
                if subset.empty:
                    logger.debug("No contract rows for strike %s", strike)
                    continue
                for instrument_key, row in subset.iterrows():
                    tasks.append(fetch_and_build(instrument_key, row))

            if not tasks:
                raise DataQualityError("No option contract tasks created for selected strikes")

            # run tasks concurrently (the semaphore controls concurrency)
            results = await asyncio.gather(*tasks, return_exceptions=False)

            calls: List[OptionData] = []
            puts: List[OptionData] = []
            for r in results:
                if r is None:
                    continue
                typ = (r.option_type or "").strip().upper()
                if typ == "CE":
                    calls.append(r)
                elif typ == "PE":
                    puts.append(r)
                else:
                    logger.debug("Unknown option_type on result: %s (treat as CE)", r.option_type)
                    calls.append(r)
            chain = OptionChain(
                symbol=symbol,
                spot_price=Decimal(str(spot_price)),
                expiry_date=expiry_date,
                call_options=sorted(calls, key=lambda x: x.strike),
                put_options=sorted(puts, key=lambda x: x.strike),
                timestamp=datetime.utcnow(),
            )
            await self.cache.set_by_key(cache_key, chain.dict(), ttl=self.CACHE_TTL)
            return chain

        except DataQualityError:
            raise
        except Exception as e:
            logger.exception("Error computing option chain for %s: %s", symbol, e)
            return self._get_fallback_option_chain(symbol, expiry_date)