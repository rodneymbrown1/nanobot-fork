import * as cdk from 'aws-cdk-lib';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';

export interface NanobotSharedStackProps extends cdk.StackProps {
  /** S3 bucket name for agent identity files (optional). */
  agentBucket?: string;
}

/**
 * Shared resources used by all nanobot instances:
 * - ECR repository
 * - IAM user with scoped permissions
 * - Org-level secret (shared API keys)
 * - Optional S3 bucket for agent identity
 */
export class NanobotSharedStack extends cdk.Stack {
  /** ECR repository URI for Docker images. */
  public readonly ecrRepo: ecr.Repository;
  /** IAM user used by all instances. */
  public readonly instanceUser: iam.User;
  /** IAM access key for instance user. */
  public readonly accessKey: iam.CfnAccessKey;
  /** Org-level secret ARN (shared LLM/integration keys). */
  public readonly orgSecret: secretsmanager.Secret;
  /** S3 bucket (if configured). */
  public readonly bucket?: s3.IBucket;

  constructor(scope: Construct, id: string, props?: NanobotSharedStackProps) {
    super(scope, id, props);

    const agentBucket = props?.agentBucket ?? this.node.tryGetContext('agentBucket') ?? '';

    // ── 1. ECR repository ───────────────────────────────────────────────
    this.ecrRepo = new ecr.Repository(this, 'NanobotRepo', {
      repositoryName: 'nanobot',
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      lifecycleRules: [
        {
          maxImageCount: 5,
          description: 'Keep last 5 images',
        },
      ],
    });

    // ── 2. Org-level secret (shared API keys) ───────────────────────────
    this.orgSecret = new secretsmanager.Secret(this, 'NanobotOrgSecret', {
      secretName: 'nanobot/org',
      description:
        'Shared nanobot org config — LLM provider keys, integrations, tools. Shared across all instances.',
      secretStringValue: cdk.SecretValue.unsafePlainText(
        JSON.stringify(
          {
            agents: {
              defaults: {
                model: 'anthropic/claude-opus-4-5',
                maxTokens: 8192,
                temperature: 0.7,
                maxToolIterations: 20,
                memoryWindow: 50,
              },
            },
            providers: {
              anthropic: { apiKey: 'REPLACE_ME' },
            },
            gateway: {
              host: '127.0.0.1',
              port: 18790,
            },
            tools: {
              restrictToWorkspace: false,
              web: {
                search: { apiKey: '' },
              },
            },
          },
          null,
          2,
        ),
      ),
    });

    // ── 3. IAM user with least-privilege access ─────────────────────────
    this.instanceUser = new iam.User(this, 'NanobotInstanceUser', {
      userName: 'nanobot-instance',
    });

    // Read org secret
    this.orgSecret.grantRead(this.instanceUser);

    // Read any instance secret under nanobot/instance/*
    this.instanceUser.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ReadInstanceSecrets',
        effect: iam.Effect.ALLOW,
        actions: [
          'secretsmanager:GetSecretValue',
          'secretsmanager:DescribeSecret',
        ],
        resources: [
          `arn:aws:secretsmanager:${this.region}:${this.account}:secret:nanobot/instance/*`,
        ],
      }),
    );

    // Read/write invite secrets (for invite system)
    this.instanceUser.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ManageInviteSecrets',
        effect: iam.Effect.ALLOW,
        actions: [
          'secretsmanager:GetSecretValue',
          'secretsmanager:DescribeSecret',
          'secretsmanager:CreateSecret',
          'secretsmanager:PutSecretValue',
          'secretsmanager:DeleteSecret',
          'secretsmanager:ListSecrets',
        ],
        resources: [
          `arn:aws:secretsmanager:${this.region}:${this.account}:secret:nanobot/invites/*`,
        ],
      }),
    );

    // Pull images from ECR
    this.ecrRepo.grantPull(this.instanceUser);
    this.instanceUser.addToPolicy(
      new iam.PolicyStatement({
        sid: 'EcrAuthToken',
        effect: iam.Effect.ALLOW,
        actions: ['ecr:GetAuthorizationToken'],
        resources: ['*'],
      }),
    );

    // ── 4. S3 bucket (optional) ─────────────────────────────────────────
    if (agentBucket) {
      this.bucket = new s3.Bucket(this, 'AgentIdentityBucket', {
        bucketName: agentBucket,
        removalPolicy: cdk.RemovalPolicy.RETAIN,
        versioned: true,
        blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
        encryption: s3.BucketEncryption.S3_MANAGED,
      });

      this.bucket.grantReadWrite(this.instanceUser);

      new cdk.CfnOutput(this, 'AgentBucketName', {
        value: agentBucket,
        description: 'S3 bucket for agent identity files',
      });
    }

    // ── 5. Access key ───────────────────────────────────────────────────
    this.accessKey = new iam.CfnAccessKey(this, 'NanobotAccessKey', {
      userName: this.instanceUser.userName,
    });

    // ── Outputs ─────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'EcrRepoUri', {
      value: this.ecrRepo.repositoryUri,
      description: 'ECR repository URI — used by all instances',
    });

    new cdk.CfnOutput(this, 'OrgSecretArn', {
      value: this.orgSecret.secretArn,
      description: 'Org-level Secrets Manager ARN — shared API keys',
    });
  }
}
