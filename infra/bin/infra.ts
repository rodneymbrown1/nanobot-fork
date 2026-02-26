#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { NanobotSharedStack } from '../lib/nanobot-shared-stack';
import { NanobotInstanceStack } from '../lib/nanobot-instance-stack';

const app = new cdk.App();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION ?? 'us-east-1',
};

const agentBucket: string = app.node.tryGetContext('agentBucket') ?? '';

// ── Shared stack (ECR, IAM, org secret, S3) ─────────────────────────────
const shared = new NanobotSharedStack(app, 'NanobotSharedStack', {
  env,
  description: 'Nanobot shared resources — ECR, IAM, org secret',
  agentBucket: agentBucket || undefined,
});

// ── Per-instance stacks ─────────────────────────────────────────────────
// Default to a single "nanobot" instance for backward compatibility.
// Override: --context instances='["nano-alpha","nano-beta"]'
const rawInstances = app.node.tryGetContext('instances');
const instances: string[] =
  typeof rawInstances === 'string' ? JSON.parse(rawInstances) : rawInstances ?? ['nanobot'];

for (const name of instances) {
  new NanobotInstanceStack(app, `Nanobot-${name}`, {
    env,
    description: `Nanobot instance: ${name}`,
    instanceName: name,
    ecrRepo: shared.ecrRepo,
    accessKeyRef: shared.accessKey.ref,
    accessKeySecret: shared.accessKey.attrSecretAccessKey,
    orgSecretArn: shared.orgSecret.secretArn,
    agentBucket: agentBucket || undefined,
  });
}
