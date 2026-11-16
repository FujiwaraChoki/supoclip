// Run inside the exact frontend container being considered for traffic.
// Exercise the same generated client and auth tables as Better Auth.
const { PrismaClient } = require(process.cwd() + "/src/generated/prisma");
const db = new PrismaClient();
const deadline = setTimeout(() => process.exit(1), 5000);
async function check() {
  try {
    await db.$transaction([
      db.user.findUnique({ where: { id: "" } }),
      db.account.findUnique({ where: { id: "" } }),
      db.session.findUnique({ where: { token: "" } }),
    ]);
  } catch {
    process.exitCode = 1;
  } finally {
    await db.$disconnect();
    clearTimeout(deadline);
  }
}
check();
