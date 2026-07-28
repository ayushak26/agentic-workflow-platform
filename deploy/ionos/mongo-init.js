const appDbName = process.env.MONGO_DB || "eurskem_ai";
const appDb = db.getSiblingDB(appDbName);

if (!appDb.getUser(process.env.MONGO_APP_USERNAME)) {
  appDb.createUser({
    user: process.env.MONGO_APP_USERNAME,
    pwd: process.env.MONGO_APP_PASSWORD,
    roles: [{ role: "readWrite", db: appDbName }],
  });
}
