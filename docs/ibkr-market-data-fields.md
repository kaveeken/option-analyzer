# IBKR Client Portal API — Market Data Fields

Reference: https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/#market-data-fields

Fields are requested via `GET /iserver/marketdata/snapshot?conids={conid}&fields={field1},{field2},...`

All fields return `string` unless noted otherwise.

## Currently Used

| Field | Name | Description |
|-------|------|-------------|
| 31 | Last Price | The last price at which the contract traded. May contain prefixes: C = Previous day's closing price, H = Trading has halted. |
| 84 | Bid Price | The highest-priced bid on the contract. |
| 86 | Ask Price | The lowest-priced offer on the contract. |

## Greeks

No Rho field is available from the IBKR API.

| Field | Name | Description |
|-------|------|-------------|
| 7308 | Delta | The ratio of the change in the price of the option to the corresponding change in the price of the underlying. |
| 7309 | Gamma | The rate of change for the delta with respect to the underlying asset's price. |
| 7310 | Theta | A measure of the rate of decline the value of an option due to the passage of time. |
| 7311 | Vega | The amount that the price of an option changes compared to a 1% change in the volatility. |

## Implied & Historical Volatility

| Field | Name | Description |
|-------|------|-------------|
| 7633 | Implied Vol. % | The implied volatility for the **specific strike** of the option in percentage. Use this on option contracts. |
| 7283 | Option Implied Vol. % | IV estimated for a maturity 30 calendar days forward, based on option prices from two consecutive expiration months. This is on the **underlying**, not a specific strike. |
| 7087 | Hist. Vol. % | 30-day real-time historical volatility on the underlying. |
| 7088 | Hist. Vol. Close % | Historical volatility based on previous close price. |
| 7084 | Implied Vol./Hist. Vol % | The ratio of the implied volatility over the historical volatility, expressed as a percentage. |

## Dividends

IBKR returns dividend amounts in dollars per share only — no yield % field exists. Compute yield as `dividends_forward / current_price`.

| Field | Name | Description |
|-------|------|-------------|
| 7671 | Dividends | Total expected dividend payments over the **next 12 months** per share. |
| 7672 | Dividends TTM | Total dividend payments over the **last 12 months** per share. |

## Options Analytics

| Field | Name | Description |
|-------|------|-------------|
| 7638 | Option Open Interest | |
| 7089 | Opt. Volume | Option volume for the day. |
| 7607 | Opt. Volume Change % | Today's option volume as a percentage of the average option volume. |
| 7695 | Break Even | Break even points. |
| 7694 | Probability of Max Return | Customer implied probability of maximum potential gain. |
| 7700 | Probability of Max Return | Customer implied probability of maximum potential gain. |
| 7702 | Probability of Max Loss | Customer implied probability of maximum potential loss. |
| 7703 | Profit Probability | Customer implied probability of any gain. |
| 7085 | Put/Call Interest | Put option open interest / call option open interest for the trading day. |
| 7086 | Put/Call Volume | Put option volume / call option volume for the trading day. |
| 7285 | Put/Call Ratio | |

## Price & OHLC

| Field | Name | Description |
|-------|------|-------------|
| 70 | High | Current day high price. |
| 71 | Low | Current day low price. |
| 7295 | Open | Today's opening price. |
| 7296 | Close | Today's closing price. |
| 7741 | Prior Close | Yesterday's closing price. |
| 7293 | 52 Week High | The highest price for the past 52 weeks. |
| 7294 | 52 Week Low | The lowest price for the past 52 weeks. |
| 7682 | Change Since Open | The difference between the last price and the open price. |
| 82 | Change | The difference between the last price and the close on the previous trading day. |
| 83 | Change % | The difference between the last price and the close on the previous trading day in percentage. |
| 7635 | Mark | Ask price if ask < last; bid price if bid > last; otherwise last price. |

## Volume

| Field | Name | Description |
|-------|------|-------------|
| 87 | Volume | Volume for the day, formatted with 'K' for thousands or 'M' for millions. |
| 7762 | Volume Long | High precision volume for the day (unformatted). |
| 7282 | Average Volume | The average daily trading volume over 90 days. |
| 88 | Bid Size | The number of contracts or shares bid for at the bid price. |
| 85 | Ask Size | The number of contracts or shares offered at the ask price. |
| 7059 | Last Size | The number of units traded at the last price. |

## Position & P&L

| Field | Name | Description |
|-------|------|-------------|
| 73 | Market Value | Current market value of your position. Calculated with real-time data. |
| 74 | Avg Price | The average price of the position. |
| 75 | Unrealized PnL | Unrealized profit or loss. Calculated with real-time data. |
| 77 | Formatted Unrealized PnL | |
| 78 | Daily PnL | Profit or loss of the day since prior close. |
| 7920 | Daily PnL Raw | Raw (unformatted) daily P&L. |
| 79 | Realized PnL | Realized profit or loss. |
| 80 | Unrealized PnL % | Unrealized profit or loss as a percentage. |
| 7292 | Cost Basis | Current position multiplied by average price and multiplier. |
| 7921 | Cost Basis Raw | Raw (unformatted) cost basis. |
| 76 | Formatted position | |
| 7639 | % of Mark Value | Market value of the contract as a percentage of total account market value. |
| 7696 | SPX Delta | Beta Weighted Delta = Delta × dollar adjusted beta. |

## Contract Metadata

| Field | Return Type | Name | Description |
|-------|-------------|------|-------------|
| 55 | string | Symbol | |
| 58 | string | Text | |
| 201 | string | Right | P for Put or C for Call. |
| 6004 | string | Exchange | |
| 6008 | integer | Conid | Contract identifier from IBKR's database. |
| 6070 | string | SecType | The asset class of the instrument. |
| 6072 | string | Months | |
| 6073 | string | Regular Expiry | |
| 6457 | integer | Underlying Conid | Use /trsrv/secdef to get more information about the security. |
| 6508 | string | Service Params. | |
| 6509 | string | Market Data Availability | Three-char field: R/D/Z/Y/N (timeline) + P/p (structure) + B (type). |
| 7051 | string | Company name | |
| 7094 | string | Conid + Exchange | |
| 7184 | string | canBeTraded | Returns 1 (true) or 0 (false). |
| 7219 | string | Contract Description | |
| 7220 | string | Contract Description | |
| 7221 | string | Listing Exchange | |
| 7714 | string | Last Trading Date | |
| 7768 | string | hasTradingPermissions | Returns 1 (true) or 0 (false). |

## Fundamentals

| Field | Name | Description |
|-------|------|-------------|
| 7280 | Industry | Industry category for the underlying company. |
| 7281 | Category | More detailed category within the industry. |

## Moving Averages

| Field | Name | Description |
|-------|------|-------------|
| 7674 | EMA(200) | Exponential moving average (N=200). |
| 7675 | EMA(100) | Exponential moving average (N=100). |
| 7676 | EMA(50) | Exponential moving average (N=50). |
| 7677 | EMA(20) | Exponential moving average (N=20). |
| 7678 | Price/EMA(200) | Price to EMA(200) ratio minus 1, as percentage. |
| 7679 | Price/EMA(100) | Price to EMA(100) ratio minus 1, as percentage. |
| 7724 | Price/EMA(50) | Price to EMA(50) ratio minus 1, as percentage. |
| 7681 | Price/EMA(20) | Price to EMA(20) ratio minus 1, as percentage. |

## Short Selling

| Field | Name | Description |
|-------|------|-------------|
| 7636 | Shortable Shares | Number of shares available for shorting. |
| 7637 | Fee Rate | Interest rate charged on borrowed shares. |
| 7644 | Shortable | Describes the level of difficulty with which the security can be sold short. |

## Events (Requires Wall Street Horizon subscription)

| Field | Name | Description |
|-------|------|-------------|
| 7683 | Upcoming Event | Shows the next major company event. |
| 7684 | Upcoming Event Date | The date of the next major company event. |
| 7685 | Upcoming Analyst Meeting | Date and time of the next scheduled analyst meeting. |
| 7686 | Upcoming Earnings | Date and time of the next scheduled earnings/earnings call event. |
| 7687 | Upcoming Misc Event | Date and time of the next shareholder meeting, presentation or other event. |
| 7688 | Recent Analyst Meeting | Date and time of the most recent analyst meeting. |
| 7689 | Recent Earnings | Date and time of the most recent earnings/earnings call event. |
| 7690 | Recent Misc Event | Date and time of the most recent shareholder meeting, presentation or other event. |

## Bonds

| Field | Name | Description |
|-------|------|-------------|
| 7697 | Futures Open Interest | Total number of outstanding futures contracts. |
| 7698 | Last Yield | Implied yield if purchased at current last price. |
| 7699 | Bid Yield | Implied yield if purchased at current bid price. |
| 7720 | Ask Yield | Implied yield if purchased at current offer. |
| 7704 | Organization Type | |
| 7705 | Debt Class | |
| 7706 | Ratings | Ratings issued for bond contract. |
| 7707 | Bond State Code | |
| 7708 | Bond Type | |
| 7715 | Issue Date | |
