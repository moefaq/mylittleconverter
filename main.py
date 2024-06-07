import aiohttp.web
from aiohttp.abc import AbstractAccessLogger
from ruamel.yaml import YAML
from ruamel.yaml.compat import StringIO
import logging
import re

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
    level=logging.INFO,
)

yaml = YAML()
yaml.width = 2000
yaml.indent = 4

with open("config.yml", "r") as f:
    config = yaml.load(f)

appsTokens = [app["token"] for app in config["apps"]]

headers = {"User-Agent": "clash"}


async def buildSubData(yamlData, apptoken):
    try:
        appConfig = next((app for app in config["apps"] if apptoken in app.values()))
        allinGroups = appConfig["allin"]

        templateData = None

        if re.match(r"^https?://", appConfig["file"]):
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    appConfig["file"], headers=headers, allow_redirects=True
                ) as response:
                    templateData = yaml.load(await response.text(encoding="utf-8"))
        else:
            with open(f"apps/{appConfig['file']}", "r") as f:
                templateData = yaml.load(f)

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
                pattern = re.compile(proxyGroup["proxies"][-1].replace("/", ""))
                proxyGroup["proxies"].pop()
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
            respYamlData = yaml.load(await response.text(encoding="utf-8"))
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
                stream = StringIO()
                yaml.dump(yamlData, stream)
                text = stream.getvalue()
                stream.close()
                return aiohttp.web.Response(
                    text=text,
                    headers=respHeaders,
                )
            else:
                return aiohttp.web.Response(text="Nothing!")


app = aiohttp.web.Application()
app.router.add_get("/", handle_request)

aiohttp.web.run_app(
    app,
    host=f"{config['server']['listen']}",
    port=config["server"]["port"],
    access_log_format='%a %t "%r" %s %b "%{Referer}i" "User-Agent: %{User-Agent}i" "X-Real-IP: %{X-Real-IP}i" "X-FORWARDED-FOR: %{X-FORWARDED-FOR}i"',
)
