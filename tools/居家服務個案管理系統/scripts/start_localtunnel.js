const fs = require("fs");
const path = require("path");
const localtunnel = require("../tools_bin/node_modules/localtunnel");

async function main() {
  const tunnel = await localtunnel({ port: 8000, local_host: "127.0.0.1" });
  const dataDir = path.join(__dirname, "..", "data");
  fs.mkdirSync(dataDir, { recursive: true });
  fs.writeFileSync(path.join(dataDir, "localtunnel-url.txt"), tunnel.url + "\n", "utf8");
  console.log(`LINE webhook tunnel: ${tunnel.url}/line/webhook`);
  tunnel.on("close", () => {
    console.log("localtunnel closed");
  });
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
