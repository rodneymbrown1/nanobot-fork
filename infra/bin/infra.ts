#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { NanobotStack } from '../lib/nanobot-stack';

const app = new cdk.App();

new NanobotStack(app, 'NanobotStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION ?? 'us-east-1',
  },
  description: 'Nanobot AI assistant on AWS Lightsail',
});
