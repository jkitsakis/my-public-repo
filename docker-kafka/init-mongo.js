// Switch to (or create) the database
db = db.getSiblingDB('preassessmentdb');

db.createUser({
  user: "mongo",
  pwd: "mongo",
  roles: [{ role: "readWrite", db: "preassessmentdb" }]
});

// Create collection if not exists
db.createCollection("preassessment_user_behaviour_logs");

// Create indexes for efficient querying
db.preassessment_user_behaviour_logs.createIndex({ timestamp: 1 });
db.preassessment_user_behaviour_logs.createIndex({ applicationName: 1 });
db.preassessment_user_behaviour_logs.createIndex({ userId: 1 });
db.preassessment_user_behaviour_logs.createIndex({ eventType: 1 });

print("preassessment_user_behaviour_logs collection created with indexes");
