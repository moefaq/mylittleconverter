import aiohttp.web
import yaml
import aiohttp
import logging
import re

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
    level=logging.INFO,
)


with open("config.yml", "r") as f:
    config = yaml.safe_load(f)

appsTokens = [app["token"] for app in config["apps"]]

headers = {"User-Agent": "clash"}


async def buildSubData(yamlData, apptoken):
    try:
        appConfig = next((app for app in config["apps"] if apptoken in app.values()))
        allinGroups = appConfig["allin"]
        with open(f"apps/{appConfig['file']}", "r") as f:
            templateData = yaml.load(f, yaml.CUnsafeLoader)

        subYamlData = templateData

        proxies = yamlData["proxies"]
        subYamlData["proxies"] = proxies

        proxiesNames = []
        for proxy in proxies:
            proxiesNames.append(proxy["name"])

        proxyGroups = templateData["proxy-groups"]
        for proxyGroup in proxyGroups:
            if proxyGroup["name"] in allinGroups:
                proxyGroup["proxies"] = proxiesNames
            if (
                proxyGroup["proxies"][-1][:2] == "/^"
                and proxyGroup["proxies"][-1][-1] == "/"
            ):
                pattern = re.compile(proxyGroup["proxies"][0].replace("/", ""))
                proxyGroup["proxies"] = []
                for proxiesName in proxiesNames:
                    if re.search(pattern, proxiesName):
                        proxyGroup["proxies"].append(proxiesName)

        subYamlData["proxy-groups"] = proxyGroups
        return subYamlData
    except Exception as err:
        logging.error(err)
        return


async def handle_request(request):
    apptoken = request.query.get("apptoken")
    url = request.query.get("url")

    if apptoken not in appsTokens:
        return aiohttp.web.Response(text="Invalid apptoken", status=401)

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, allow_redirects=True) as response:
            respYamlData = yaml.full_load(await response.text(encoding="utf-8"))
            yamlData = await buildSubData(respYamlData, apptoken)
            if yamlData is not None:
                respHeaders = {
                    "subscription-userinfo": response.headers["subscription-userinfo"],
                    "profile-update-interval": response.headers[
                        "profile-update-interval"
                    ],
                    "content-disposition": response.headers["content-disposition"],
                    "profile-web-page-url": response.headers["profile-web-page-url"],
                }
                return aiohttp.web.Response(
                    text=yaml.safe_dump(
                        data=yamlData, encoding="utf-8", width=400
                    ).decode("unicode-escape"),
                    headers=respHeaders,
                )
            else:
                return aiohttp.web.Response(text="Nothing!")


app = aiohttp.web.Application()
app.router.add_get("/", handle_request)

aiohttp.web.run_app(
    app, host=f"{config['server']['listen']}", port=config["server"]["port"]
)
