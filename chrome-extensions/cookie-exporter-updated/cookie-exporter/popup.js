function cookieHeaderFromCookies(cookies) {
  return cookies
    .map((c) => `${c.name}=${c.value}`)
    .join("; ");
}

function isAuthCookie(cookie) {
  const name = cookie.name || "";
  return name === ".AspNet.Cookies" || name.toUpperCase().startsWith("NSC");
}

function getActiveTabUrl() {
  return new Promise((resolve, reject) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const tab = tabs && tabs[0];
      if (!tab || !tab.url) {
        reject(new Error("No active tab URL found"));
        return;
      }
      resolve(tab.url);
    });
  });
}

function getCookiesForUrl(url) {
  return new Promise((resolve) => {
    chrome.cookies.getAll({ url }, (cookies) => {
      resolve(cookies || []);
    });
  });
}

async function loadDomainCookies() {
  const authOutput = document.getElementById("authCookieOutput");
  const domainOutput = document.getElementById("domainCookieOutput");
  const status = document.getElementById("status");

  try {
    const url = await getActiveTabUrl();
    const cookies = await getCookiesForUrl(url);

    const authCookies = cookies.filter(isAuthCookie);

    authOutput.value = authCookies.length
      ? cookieHeaderFromCookies(authCookies)
      : "[No .AspNet.Cookies or NSC* cookies found for active tab]";

    domainOutput.value = cookies.length
      ? cookieHeaderFromCookies(cookies)
      : "[No cookies found for active tab domain]";

    status.textContent = `Loaded ${authCookies.length} auth cookies / ${cookies.length} total cookies`;
  } catch (err) {
    authOutput.value = `[Error] ${err.message}`;
    domainOutput.value = `[Error] ${err.message}`;
    status.textContent = "Failed to load cookies";
  }
}

function loadSentCookies() {
  const sentOutput = document.getElementById("sentCookieOutput");
  chrome.storage.local.get(["lastSentCookies", "lastSentUrl", "lastSentAt"], (data) => {
    const header = data.lastSentCookies || "[No request captured yet]";
    const meta = data.lastSentUrl ? `# ${data.lastSentAt || ""}\n# ${data.lastSentUrl}\n` : "";
    sentOutput.value = meta + header;
  });
}

function copyText(textareaId, button) {
  const value = document.getElementById(textareaId).value;
  navigator.clipboard.writeText(value).then(() => {
    const old = button.textContent;
    button.textContent = "Copied!";
    setTimeout(() => (button.textContent = old), 1500);
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  const copyAuthBtn = document.getElementById("copyAuthButton");
  const copyDomainBtn = document.getElementById("copyDomainButton");
  const downloadBtn = document.getElementById("downloadButton");
  const refreshBtn = document.getElementById("refreshButton");

  copyAuthBtn.addEventListener("click", () => copyText("authCookieOutput", copyAuthBtn));
  copyDomainBtn.addEventListener("click", () => copyText("domainCookieOutput", copyDomainBtn));

  refreshBtn.addEventListener("click", () => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const tabId = tabs[0].id;
      chrome.tabs.reload(tabId, {}, () => {
        setTimeout(() => {
          chrome.scripting.executeScript({ target: { tabId }, files: ["inject.js"] });
          setTimeout(() => {
            loadDomainCookies();
            loadSentCookies();
          }, 1500);
        }, 1000);
      });
    });
  });

  downloadBtn.addEventListener("click", () => {
    const value = document.getElementById("authCookieOutput").value;
    const blob = new Blob([value], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "auth_cookies.txt";
    a.click();
    URL.revokeObjectURL(url);
  });

  await loadDomainCookies();
  loadSentCookies();
});
