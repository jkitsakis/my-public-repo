chrome.webRequest.onBeforeSendHeaders.addListener(
  (details) => {
    const cookieHeader = (details.requestHeaders || []).find(
      (h) => h.name && h.name.toLowerCase() === "cookie"
    );

    if (cookieHeader) {
      chrome.storage.local.set({
        lastSentCookies: cookieHeader.value,
        lastSentUrl: details.url,
        lastSentAt: new Date().toISOString()
      });
      console.log("✅ Captured Cookie header:", cookieHeader.value);
    }
  },
  { urls: ["<all_urls>"] },
  ["requestHeaders", "extraHeaders"]
);
