## Proposal of MVP version of Bot Blitzer

### Background

We are likely to lose motivation on this project.  I think the best way for this project to stay interesting, is if we can use some form of what we are building almost immediately.  The purpose of this doc is to describe the MVP version of Bot Blitzer which we can start playing immediately, competing in algorithmic fantasy sports play.

### Approach

We focus on NBA because the NBA has games daily until June.  

Every day, each player is asked to respond to 1 or more prop bets of this format
```json
{
    "type": "over",
    "player": "1",
    "points": "20",
    "odds": {
        "denominator": 4,
        "numerator": 270,
    },
}
```
And they are asked to response with whether they'd like to take the best
```json 
{
    "reason": "", \\ displayed but otherwise unused
    "quantity": -4.0,  \\ zero indicates a "skip", negative indicates a "No" bet of a specified magnitude, positive indicates a "Yes" bet; invalid if wagering an amount outside of the user's current wallet
}
```

Each team will start off with the random decision as their predict function (though they can change it at any time)
```python
import random

predict(bet Bet) response {
    random_value = random.random()  # Generates a random float between 0 and 1
    
    if random_value > 0.75:
        return "YES"
    elif random_value < 0.25:
        return "NO"
    else:
        return "SKIP"
}
```

Every day, a bet is played out.  To provide a user experience, automated messages will be sent out via Discord.

### Justification

This will be interesting even when it's fully random.  The game will get started a quick and we can steer it in whatever way we have time for.

## Appendix

### Where to get the bets:

ESPN Bet can be called with
```bash
curl -X POST "https://sportsbook-espnbet.us-default.thescore.bet/graphql/persisted_queries/8103d287a4914d311ef3cd7dcc73a4f849bd21e36be8cc4e7b56a56029d8f2e5" \
-H "Accept: application/json" \
-H "Accept-Encoding: gzip, deflate, br" \
-H "Accept-Language: en-US,en;q=0.9" \
-H "Content-Type: application/json" \
-H "Cookie: __cf_bm=92RFB_.1ufW7AyPeciCjLv10MjWl05INTptK6nfaa2s-1707024569-1-AYrIgsY7svZIT8qnODnrdx+grVrDVILHqAntdaF6jE62O4B6VZojViEfEWrt+LF2diZFu17p9iBMNrbtFwdPEIw=; __cfwaitingroom=ChhVeUpJZnBCaTlUczQyK3pmTzVGdjdnPT0ShAJBNVZIanhjek9OTXMxcktBVXVTVXp6dE1LeWtpdGxUdlZHci9aNU5MRWE4SVFNdUcxUXc3L29wYU40OXRiVmpxZTlnNXRScmZwZ1pZYkNZZzA0ckRIY3k4UEszUHhrSXdSVFRMbUtsZ29WVCtNNy9PU3htTVFTUUo5TDQ3VWhtbFlSVjlGd1c4WVRjck9TaXF0czIzYjF2TVNYMzFkbVUxd3RBQjVzRFhnalNrU0Urd3dYRGx6SzFHUzc4TEY3azR3K3Z0ZytSbEhZRkF5NjhycUZsN21GRExCTDRmczM3UHhHK2tkeVFibE9QNTYzN2VmbVR0citTTFcxYkdpNHdBWXZvPQ%3D%3D" \
-H "Origin: https://espnbet.com" \
-H "Referer: https://espnbet.com/" \
-H 'Sec-Ch-Ua: "Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"' \
-H "Sec-Ch-Ua-Mobile: ?0" \
-H 'Sec-Ch-Ua-Platform: "macOS"' \
-H "Sec-Fetch-Dest: empty" \
-H "Sec-Fetch-Mode: cors" \
-H "Sec-Fetch-Site: cross-site" \
-H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
-H "X-Anonymous-Authorization: Bearer eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhDQkMtSFMyNTYifQ.s781_ZYS4nzawI8AmjYf81v0wv_CyXfQnWSYZxiWHnGRzcFTocvA4Dq6Z1qeZ6ms4zGQLaKRYHcIBkw21a2ROAekTPH_GHk-3TGmXZo7YmnhH7h6SUFTtT1uGQpZBwL3tHHLyQ-OH2ssmuHDDlRC2tGjOy85PQNhweRMvvOJMX1r-JDiKPiHcLKyjbxwJcX2bTfsJ-ZaM4hS59XrdvONtSI7j26e5dde4O4EjKSBkQLlRAkNcZb9yOyLxYB4vP-sxB2Xk2NDznh2xdHIm3VXKflkVEP9368VN9V-h-FEXTrCNDictdoDjPn4_TOta9P2iQhfnfCXj_29F9k2FLVB4JdxHVUlHZrHTQv7FSj7r8UU8_RBlkIZMrzh1X_z4HYWhWpGRNeMH-vwF8oVneLUY_qQVRYivCm7ca2Jgu0wsAcNtWT4mDGShRiBVRFt436KR3JzdGky8S8IoeMIHXx-nukgUBSyXbibz37XQh3oI7FciecGerEZuTvUSlos-mmIo7guHPvI-6w_IITJ-w-bcamWoI1-fmKuNqKoDu5LaoM-EcsCiCiNful5-AnwrodEhcedQlAi4bh_sv-yTaACtZBWyXAkxHDPs8etz7Zq5nkR6XU06euZxJwwBN7RWzarNzooeb8CotOZEQ9tO1t-OHeEQtTzyX3s7yC1gIgkhyI.u2gcepUpaAqjG3D0WAjUrg.-nDLyOIpv5EeFuPy57Vh7ZRXWzXPRglHaUBzcEjeLtD_I-UHW0DBBVzT2DJEAq0-bvklaqjVQQQMs1JOeWMl8zlqTRFj4y8rmVLZWs1v_6YWVvSf_bhhUVPD5vZk5xPJIqeqYNUEuwwBYjLm-TQRf7Se6nb59j5z8xExPVImZ9IgccODp9fNamnCy6PPrx8JwIqFD8-IYD1WE3dESN1bcWeZFmuDcx9liaqJG6p5FsA1HAPGHPoW9kR__PRCa6ZAmJkFpTlnEXfIo66ZmVA7rQ.LT2V2UP1j0jmE2spqc9rMg" \
-H "X-App: espnbet" \
-H "X-App-Version: 24.2.0" \
-H "X-Platform: web" \
-d '{"operationName":"SeeAllLinesModal","variables":{"includeMediaUrl":false,"includeRichEvent":true,"includeSectionDefaultField":true,"canonicalUrl":"espn-sportsbook:/market_card/2df9e735-b702-4a72-b56c-89fe86267878/markets:all","oddsFormat":"AMERICAN","pageType":"MARKET_CARD_PAGE"},"extensions":{"persistedQuery":{"version":1,"sha256Hash":"8103d287a4914d311ef3cd7dcc73a4f849bd21e36be8cc4e7b56a56029d8f2e5"}}}' \
--output - | brotli --decompress - | jq
```
Which response in the format 
```json
{
    "__typename": "Market",
    "extraInformation": null,
    "id": "Market:1d3ac385-7567-4e5b-a6b2-7f8da50c57eb",
    "name": "Paolo Banchero Total Points",
    "selections": [
        {
            "__typename": "MarketSelection",
            "id": "MarketSelection:ecd5e2d7-ac68-4fd5-ac87-be865edd087e",
            "name": {
                "__typename": "SelectionName",
                "cleanName": "Over",
                "defaultName": "O 20.5",
                "fullName": "Over 20.5",
                "minimalName": "O 20.5"
            },
            "odds": {
                "__typename": "Odds",
                "denominator": 19,
                "formattedOdds": "-475",
                "numerator": 23
            },
            "participant": null,
            "points": {
                "__typename": "Points",
                "decimalPoints": 20.5,
                "formattedPoints": "20.5"
            },
            "status": "OPEN",
            "type": "OVER"
        },
        {
            "__typename": "MarketSelection",
            "id": "MarketSelection:327a6329-9687-42a1-b31a-726f6b6c4bd2",
            "name": {
                "__typename": "SelectionName",
                "cleanName": "Under",
                "defaultName": "U 20.5",
                "fullName": "Under 20.5",
                "minimalName": "U 20.5"
            },
            "odds": {
                "__typename": "Odds",
                "denominator": 4,
                "formattedOdds": "+275",
                "numerator": 15
            },
            "participant": null,
            "points": {
                "__typename": "Points",
                "decimalPoints": 20.5,
                "formattedPoints": "20.5"
            },
            "status": "OPEN",
            "type": "UNDER"
        }
    ],
    "startTime": null,
    "statisticAccrued": null,
    "status": "OPEN",
    "type": "TOTAL"
}
```