import io
import aiohttp.client_reqrep
import aiohttp.web
import aiohttp.web_request
import ruamel.yaml
import logging
import re
import configparser


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
    level=logging.INFO,
)


class noAliasRTRepresenter(ruamel.yaml.RoundTripRepresenter):
    def ignore_aliases(self, data) -> bool:
        return True


class CaseInsensitiveDict(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for key in list(self.keys()):
            value = super().pop(key)
            self.__setitem__(key, value)

    def __setitem__(self, key, value):
        super().__setitem__(key.lower(), value)

    def __getitem__(self, key):
        return super().__getitem__(key.lower())

    def __contains__(self, key):
        return super().__contains__(key.lower())

    def pop(self, key, *args, **kwargs):
        return super().pop(key.lower(), *args, **kwargs)

    def get(self, key, *args, **kwargs):
        return super().get(key.lower(), *args, **kwargs)


class noLowerCaseConfigpaser(configparser.ConfigParser):
    def optionxform(self, optionstr):
        return optionstr


yaml = ruamel.yaml.YAML()
yaml.width = 2000
yaml.indent = 4
yaml.Representer = noAliasRTRepresenter

with open("config.yml", "r") as f:
    config = yaml.load(f)

appsTokens = [app["token"] for app in config["apps"]]


async def surgeConvertor(dataText: str, appToken: str, originalReqHeaders: dict[str, str], requestUrl: str) -> str | None:
    def loadSurgeConfig(dataText: str) -> tuple[noLowerCaseConfigpaser, str] | None:
        try:
            surgeConfig = noLowerCaseConfigpaser(
                delimiters=("="),
                allow_no_value=True,
                strict=False,
                comment_prefixes=(";"),
            )
            managedFlag = ""
            lines = dataText.splitlines(True)
            for i, line in enumerate(lines):
                if re.search("#!MANAGED-CONFIG", line):
                    managedFlag = line
                    continue
                elif not line.startswith("#"):
                    text = "".join(lines[i:])
                    break
                else:
                    continue

            surgeConfig.read_string(text)
            return surgeConfig, managedFlag
        except Exception as err:
            logging.warning(err)
            return

    try:
        originalSurge = loadSurgeConfig(dataText)
        if originalSurge is None:
            return
        originalSurgeConfig, _ = originalSurge

        template = templateSelector(appToken, "surge")
        if template is None:
            return
        else:
            appname, templateFile = template

        templateSurgeConfig = None

        if re.match(r"^https?://", templateFile):
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    templateFile, headers=originalReqHeaders, allow_redirects=True
                ) as response:
                    templateSurge = loadSurgeConfig(await response.text(encoding="utf-8"))
                    if templateSurge is None:
                        return
                    else:
                        templateSurgeConfig, managedFlag = templateSurge
        else:
            with open(f"apps/{appname}/{templateFile}", "r") as f:
                templateSurge = loadSurgeConfig(f.read())
                if templateSurge is None:
                    return
                else:
                    templateSurgeConfig, managedFlag = templateSurge

        surgeConfig = templateSurgeConfig

        if "Panel" in originalSurgeConfig:
            surgeConfig["Panel"] = originalSurgeConfig["Panel"]

        tempDict = []
        for proxyName, proxyValue in originalSurgeConfig["Proxy"].items():
            if proxyValue in {"DIRECT", "REJECT", "direct", "reject"}:
                tempDict.append((proxyName, proxyValue))
        for kv in tempDict:
            originalSurgeConfig["Proxy"].pop(kv[0])
        surgeConfig["Proxy"] = originalSurgeConfig["Proxy"]

        stream = io.StringIO()
        surgeConfig.write(stream)
        surgeText = stream.getvalue()
        stream.close()

        if managedFlag != "":
            managedFlag = managedFlag.replace("$subs_link", requestUrl)
            return f"{managedFlag}{surgeText}"
        else:
            return surgeText

    except Exception as err:
        logging.warning(err)
        return


async def clashConvertor(dataText: str, appToken: str, originalReqHeaders: dict[str, str]) -> str | None:

    try:
        originalYamlData = yaml.load(dataText)
        template = templateSelector(appToken, "clash")
        if template is None:
            return
        else:
            appname, templateFile = template

        templateData = None

        if re.match(r"^https?://", templateFile):
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    templateFile, headers=originalReqHeaders, allow_redirects=True
                ) as response:
                    templateData = yaml.load(await response.text(encoding="utf-8"))
        else:
            with open(f"apps/{appname}/{templateFile}", "r") as f:
                templateData = yaml.load(f)

        yamlData = templateData

        proxies = originalYamlData["proxies"]
        yamlData["proxies"] = proxies

        proxiesNames = []
        for proxy in proxies:
            proxiesNames.append(proxy["name"])

        proxyGroups = templateData["proxy-groups"]
        for proxyGroup in proxyGroups:
            if len(proxyGroup["proxies"]) == 0:
                proxyGroup["proxies"] = proxiesNames
            if (
                proxyGroup["proxies"][-1][:2] == "/^"
                and proxyGroup["proxies"][-1][-1] == "/"
            ):
                pattern = re.compile(
                    proxyGroup["proxies"][-1].replace("/", ""))
                proxyGroup["proxies"].pop()
                for proxiesName in proxiesNames:
                    if re.search(pattern, proxiesName):
                        proxyGroup["proxies"].append(proxiesName)

        yamlData["proxy-groups"] = proxyGroups

        stream = io.StringIO()
        yaml.dump(yamlData, stream)
        clashText = stream.getvalue()
        stream.close()

        return clashText
    except Exception as err:
        logging.warning(err)
        return


def templateSelector(appToken: str, templateType: str) -> tuple[str, str] | None:
    try:
        appConfig = next(
            (app for app in config["apps"] if appToken in app.values()))
        appName = appConfig["name"]
        template = next(
            (template for template in appConfig["templates"] if template["type"] == templateType))
        return (appName, template["file"])
    except Exception as err:
        logging.warning("Something wrong while selecting template")
        return


async def fetchOriginalData(session: aiohttp.ClientSession, url: str, headers: dict[str, str]) -> tuple[str, dict[str, str]]:
    async with session.get(url, headers=headers, allow_redirects=True) as response:
        return await response.text(encoding="utf-8"), dict(response.headers)


async def processSubData(dataText: str, originalReqHeaders: dict[str, str], appToken: str, requestUrl: str) -> str | None:
    if re.search("clash", originalReqHeaders["User-Agent"], re.IGNORECASE):
        responseData = await clashConvertor(dataText, appToken, originalReqHeaders)
    elif re.search("surge", originalReqHeaders["User-Agent"],re.IGNORECASE):
        responseData = await surgeConvertor(dataText, appToken, originalReqHeaders, requestUrl)

    if responseData:
        return responseData
    else:
        return


def createResponseHeaders(originalHeaders: dict[str, str], requestUA: str) -> dict[str, str] | None:
    headers = CaseInsensitiveDict(originalHeaders)

    if re.search("clash", requestUA):
        return {
            "subscription-userinfo": headers["subscription-userinfo"],
            "profile-update-interval": headers[
                "profile-update-interval"
            ],
            "content-disposition": headers["content-disposition"],
            "profile-web-page-url": headers["profile-web-page-url"],
        }
    if re.search("surge", requestUA):
        return {
            "content-disposition": headers["content-disposition"],
        }
    return


async def handle_request(request: aiohttp.web_request.Request):
    appToken = request.query.get("apptoken")
    subUrl = request.query.get("url")
    originalReqHeaders = dict(request.headers)
    originalReqHeaders.pop("Host", None)
    requestUrl = str(request.url)
    requestUA = request.headers.get("User-Agent")

    if appToken not in appsTokens or appToken is None:
        return aiohttp.web.Response(text="Invalid appToken", status=401)
    if subUrl is None:
        return aiohttp.web.Response(text="Invalid Subscription Url", status=401)
    if requestUA is None:
        async with aiohttp.ClientSession() as session:
            async with session.get(subUrl, allow_redirects=True) as response:
                return aiohttp.web.Response(text=await response.text(encoding="utf-8"), status=200)

    async with aiohttp.ClientSession() as session:
        originalSubData, originalRespHeaders = await fetchOriginalData(session, subUrl, originalReqHeaders)
        headers = createResponseHeaders(originalRespHeaders, requestUA)
        if headers is None:
            headers = {}
        subData = await processSubData(originalSubData, originalReqHeaders, appToken, requestUrl)

        if subData:
            return aiohttp.web.Response(
                text=subData,
                status=200,
                headers=headers,
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
