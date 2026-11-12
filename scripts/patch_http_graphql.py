"""One-off patch: run GraphQL calls through the browser context HTTP client.

The Playerok SPA pegs the CPU in a small container, so JS fetch calls inside
the page never finish. The context request client shares the cookies and has
proper timeouts.
"""

from pathlib import Path

path = Path("app/playerok/client.py")
source = path.read_text(encoding="utf-8")

# ---------------------------------------------------------------- _graphql
old_graphql = '''        result = await self._run_graphql_fetch(
            page,
            """
            async ({endpoint, query, variables, operationName}) => {
              const headers = {'content-type': 'application/json'};
              if (operationName) {
                headers['apollo-require-preflight'] = 'true';
                headers['x-apollo-operation-name'] = operationName;
              }
              const body = {query, variables};
              if (operationName) body.operationName = operationName;
              const controller = new AbortController();
              const timer = setTimeout(() => controller.abort(), 30000);
              let response;
              try {
                response = await fetch(endpoint, {
                signal: controller.signal,
                method: 'POST',
                credentials: 'include',
                headers,
                body: JSON.stringify(body)
              });
              } catch (e) {
                clearTimeout(timer);
                return {status: 0, text: 'fetch failed: ' + String(e), json: null};
              }
              clearTimeout(timer);
              const text = await response.text();
              let json = null;
              try { json = JSON.parse(text); } catch (e) { json = null; }
              return {status: response.status, text: text.slice(0, 4000), json};
            }
            """,
            {
                "endpoint": endpoint,
                "query": query,
                "variables": variables or {},
                "operationName": operation_name or "",
            },
        )'''
new_graphql = '''        headers = {"content-type": "application/json"}
        if operation_name:
            headers["apollo-require-preflight"] = "true"
            headers["x-apollo-operation-name"] = operation_name
        body: dict[str, Any] = {"query": query, "variables": variables or {}}
        if operation_name:
            body["operationName"] = operation_name
        result = await self._run_graphql_request(
            page,
            "POST",
            self._absolute(endpoint),
            headers=headers,
            json_body=body,
        )'''
assert source.count(old_graphql) == 1, source.count(old_graphql)
source = source.replace(old_graphql, new_graphql, 1)

# -------------------------------------------------------- _graphql_persisted
old_persisted = '''        result = await self._run_graphql_fetch(
            page,
            """
            async ({endpoint, operationName, variables, sha256Hash}) => {
              const params = new URLSearchParams({
                operationName,
                variables: JSON.stringify(variables),
                extensions: JSON.stringify({
                  persistedQuery: {version: 1, sha256Hash}
                })
              });
              const controller = new AbortController();
              const timer = setTimeout(() => controller.abort(), 30000);
              let response;
              try {
                response = await fetch(endpoint + '?' + params.toString(), {
                signal: controller.signal,
                method: 'GET',
                credentials: 'include',
                headers: {
                  'apollo-require-preflight': 'true',
                  'x-apollo-operation-name': operationName
                }
              });
              } catch (e) {
                clearTimeout(timer);
                return {status: 0, text: 'fetch failed: ' + String(e), json: null};
              }
              clearTimeout(timer);
              const text = await response.text();
              let json = null;
              try { json = JSON.parse(text); } catch (e) { json = null; }
              return {status: response.status, text: text.slice(0, 4000), json};
            }
            """,
            {
                "endpoint": endpoint,
                "operationName": operation_name,
                "variables": variables,
                "sha256Hash": sha256_hash,
            },
        )'''
new_persisted = '''        result = await self._run_graphql_request(
            page,
            "GET",
            self._absolute(endpoint),
            headers={
                "apollo-require-preflight": "true",
                "x-apollo-operation-name": operation_name,
            },
            params={
                "operationName": operation_name,
                "variables": json.dumps(variables),
                "extensions": json.dumps(
                    {"persistedQuery": {"version": 1, "sha256Hash": sha256_hash}}
                ),
            },
        )'''
assert source.count(old_persisted) == 1, source.count(old_persisted)
source = source.replace(old_persisted, new_persisted, 1)

# ------------------------------------------------------- _create_item_mutation
old_create = '''        result = await self._run_graphql_fetch(
            page,
            """
            async ({endpoint, operations, map, images}) => {
              const form = new FormData();
              form.append('operations', JSON.stringify(operations));
              form.append('map', JSON.stringify(map));
              for (let index = 0; index < images.length; index++) {
                const [base64, mime] = images[index];
                const binary = atob(base64);
                const bytes = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
                form.append(
                  String(index + 1),
                  new File([bytes], 'item' + (index + 1) + '.jpg', {type: mime || 'image/jpeg'})
                );
              }
              const controller = new AbortController();
              const timer = setTimeout(() => controller.abort(), 30000);
              let response;
              try {
                response = await fetch(endpoint, {
                signal: controller.signal,
                method: 'POST',
                credentials: 'include',
                headers: {
                  'apollo-require-preflight': 'true',
                  'x-apollo-operation-name': 'createItem',
                },
                body: form,
              });
              } catch (e) {
                clearTimeout(timer);
                return {status: 0, text: 'fetch failed: ' + String(e), json: null};
              }
              clearTimeout(timer);
              const text = await response.text();
              let json = null;
              try { json = JSON.parse(text); } catch (e) { json = null; }
              return {status: response.status, text: text.slice(0, 4000), json};
            }
            """,
            {
                "endpoint": endpoint,
                "operations": operations,
                "map": map_data,
                "images": images,
            },
        )'''
new_create = '''        multipart: dict[str, Any] = {
            "operations": json.dumps(operations),
            "map": json.dumps(map_data),
        }
        for index, (base64, mime) in enumerate(images):
            multipart[str(index + 1)] = {
                "name": f"item{index + 1}.jpg",
                "mimeType": mime or "image/jpeg",
                "buffer": base64.b64decode(base64),
            }
        result = await self._run_graphql_request(
            page,
            "POST",
            self._absolute(endpoint),
            headers={
                "apollo-require-preflight": "true",
                "x-apollo-operation-name": "createItem",
            },
            multipart=multipart,
        )'''
assert source.count(old_create) == 1, source.count(old_create)
source = source.replace(old_create, new_create, 1)

# ------------------------------------------------------- new helper methods
old_helper = '''    async def _run_graphql_fetch(
        self, page: Page, script: str, argument: dict[str, Any]
    ) -> dict[str, Any]:
        """Run a GraphQL fetch and wait out the anti-bot challenge if needed.

        Playerok answers 401/403 on the API while its browser verification
        page is shown. The verification resolves itself, so the call is
        repeated once after the wait.
        """
        result = await self._evaluate_with_retry(page, script, argument)
        status = int(result.get("status") or 0)
        if status in (401, 403):
            logger.info("GraphQL got %s, waiting for the browser check", status)
            await self._wait_for_challenge(page)
            result = await self._evaluate_with_retry(page, script, argument)
        return result
'''
new_helper = '''    async def _run_graphql_request(
        self,
        page: Page,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        multipart: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call the GraphQL API through the browser context HTTP client.

        The context client shares the session cookies, works without the
        (CPU hungry) Playerok SPA and has real timeouts.
        """
        request = page.context.request
        try:
            if method == "GET":
                response = await request.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.config.browser_timeout_ms,
                    fail_on_status_code=False,
                )
            else:
                if multipart is not None:
                    response = await request.post(
                        url,
                        multipart=multipart,
                        headers=headers,
                        timeout=self.config.browser_timeout_ms,
                        fail_on_status_code=False,
                    )
                else:
                    response = await request.post(
                        url,
                        data=json.dumps(json_body or {}),
                        headers=headers,
                        timeout=self.config.browser_timeout_ms,
                        fail_on_status_code=False,
                    )
        except Exception as exc:  # noqa: BLE001
            return {"status": 0, "text": f"request failed: {exc}", "json": None}

        status = int(response.status)
        text = await response.text()
        payload = None
        try:
            payload = json.loads(text)
        except Exception:  # noqa: BLE001
            payload = None
        if status in (401, 403) and payload is None:
            # A plain text 401/403 means the anti-bot page answered instead of
            # the API. The browser check is retried once.
            logger.info("GraphQL got %s, waiting for the browser check", status)
            await self._wait_for_challenge(page)
            return await self._run_graphql_request(
                page,
                method,
                url,
                headers=headers,
                params=params,
                json_body=json_body,
                multipart=multipart,
            )
        return {"status": status, "text": text[:4000], "json": payload}
'''
assert source.count(old_helper) == 1, source.count(old_helper)
source = source.replace(old_helper, new_helper, 1)

# json is needed now
if "\nimport json\n" not in source:
    source = source.replace(
        "import asyncio\nimport logging\n", "import asyncio\nimport json\nimport logging\n", 1
    )

path.write_text(source, encoding="utf-8")
print("patched")
