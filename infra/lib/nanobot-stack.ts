import * as cdk from 'aws-cdk-lib';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lightsail from 'aws-cdk-lib/aws-lightsail';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as fs from 'fs';
import * as path from 'path';
import { Construct } from 'constructs';

export class NanobotStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Context-driven sizing — override via cdk.json or `--context` flags
    const az: string = this.node.tryGetContext('availabilityZone') ?? `${this.region}a`;
    const bundleId: string = this.node.tryGetContext('bundleId') ?? 'small_3_0';
    const diskSizeGb: number = this.node.tryGetContext('diskSizeGb') ?? 20;

    // SSH CIDR — REQUIRED. No default. Restrict to your office/home IP.
    // Pass via: cdk deploy --context sshCidrs='["1.2.3.4/32"]'
    const rawCidrs = this.node.tryGetContext('sshCidrs');
    const sshCidrs: string[] | undefined =
      typeof rawCidrs === 'string' ? JSON.parse(rawCidrs) : rawCidrs;
    if (!sshCidrs || sshCidrs.length === 0) {
      throw new Error(
        'Missing required context: sshCidrs. ' +
        'Pass your IP CIDR to restrict SSH access: ' +
        '--context sshCidrs=\'["YOUR_IP/32"]\''
      );
    }

    // ── 1. ECR repository ─────────────────────────────────────────────────────
    // The Docker image is built locally and pushed here before deploying.
    // Repository is RETAINED on stack destroy to avoid accidental image loss.
    const ecrRepo = new ecr.Repository(this, 'NanobotRepo', {
      repositoryName: 'nanobot',
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      lifecycleRules: [
        {
          maxImageCount: 5,
          description: 'Keep last 5 images',
        },
      ],
    });

    // ── 2. Secrets Manager: nanobot config ────────────────────────────────────
    // Stores the full nanobot config.json. Update REPLACE_ME values after deploy
    // using: aws secretsmanager put-secret-value --secret-id nanobot/config --secret-string file://config.json
    const configSecret = new secretsmanager.Secret(this, 'NanobotConfig', {
      secretName: 'nanobot/config',
      description:
        'Nanobot AI assistant configuration. Replace REPLACE_ME values with real credentials.',
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
            channels: {
              telegram: {
                enabled: false,
                token: 'REPLACE_ME',
                allowFrom: [],
              },
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

    // ── 3. IAM user with least-privilege access ───────────────────────────────
    // The instance uses this identity to read the config secret and pull images.
    // Lightsail does not support EC2-style instance profiles, so we use a
    // dedicated IAM user with a scoped access key injected via user data.
    const instanceUser = new iam.User(this, 'NanobotInstanceUser', {
      userName: 'nanobot-instance',
    });

    // Read the config secret
    configSecret.grantRead(instanceUser);

    // Pull images from ECR
    ecrRepo.grantPull(instanceUser);
    instanceUser.addToPolicy(
      new iam.PolicyStatement({
        sid: 'EcrAuthToken',
        effect: iam.Effect.ALLOW,
        actions: ['ecr:GetAuthorizationToken'],
        resources: ['*'],
      }),
    );

    // Access key — injected into the instance via CloudFormation user data
    const accessKey = new iam.CfnAccessKey(this, 'NanobotAccessKey', {
      userName: instanceUser.userName,
    });

    // ── 4. Persistent data disk ───────────────────────────────────────────────
    // All ~/.nanobot state (sessions, memory, config, WhatsApp auth) lives here.
    // The disk persists independently of the instance — safe to stop/restart.
    const disk = new lightsail.CfnDisk(this, 'NanobotDisk', {
      diskName: 'nanobot-data',
      sizeInGb: diskSizeGb,
      availabilityZone: az,
    });

    // ── 5. User data (instance bootstrap script) ──────────────────────────────
    // CloudFormation's Fn::Sub variable map does NOT support Fn::GetAtt values
    // (e.g. the IAM access key's SecretAccessKey attribute). We substitute CDK
    // tokens directly into the string instead; CDK synthesises multi-token
    // strings as Fn::Join, which does support Fn::GetAtt.
    const userDataTemplate = fs.readFileSync(
      path.join(__dirname, 'scripts', 'user-data.sh'),
      'utf-8',
    );

    const userDataScript = userDataTemplate
      .replace(/\$\{AWSAccessKeyId\}/g, accessKey.ref)
      .replace(/\$\{AWSSecretKey\}/g, accessKey.attrSecretAccessKey)
      .replace(/\$\{SecretArn\}/g, configSecret.secretArn)
      .replace(/\$\{AWS::AccountId\}/g, cdk.Aws.ACCOUNT_ID)
      .replace(/\$\{AWS::Region\}/g, cdk.Aws.REGION);

    // ── 6. Lightsail instance ─────────────────────────────────────────────────
    const instance = new lightsail.CfnInstance(this, 'NanobotInstance', {
      instanceName: 'nanobot',
      availabilityZone: az,
      blueprintId: 'ubuntu_22_04',
      bundleId,
      userData: userDataScript,
      networking: {
        ports: [
          // SSH — restricted to operator-specified CIDRs (no default)
          { fromPort: 22, toPort: 22, protocol: 'tcp', cidrs: sshCidrs },
          // HTTP — needed for Let's Encrypt ACME challenges
          { fromPort: 80, toPort: 80, protocol: 'tcp', cidrs: ['0.0.0.0/0'] },
          // HTTPS — public TLS endpoint (proxied to port 18790 by nginx)
          { fromPort: 443, toPort: 443, protocol: 'tcp', cidrs: ['0.0.0.0/0'] },
          // Port 18790 is intentionally NOT opened — nginx handles ingress
        ],
      },
      hardware: {
        disks: [
          {
            diskName: disk.diskName,
            path: '/dev/xvdf',
            isSystemDisk: false,
          },
        ],
      },
      addOns: [
        {
          // Automatic daily snapshots at 06:00 UTC
          addOnType: 'AutoSnapshot',
          autoSnapshotAddOnRequest: {
            snapshotTimeOfDay: '06:00',
          },
        },
      ],
    });

    instance.addDependency(disk);

    // ── 7. Static IP ──────────────────────────────────────────────────────────
    // Ensures the public IP survives instance stop/start cycles.
    const staticIp = new lightsail.CfnStaticIp(this, 'NanobotStaticIp', {
      staticIpName: 'nanobot-ip',
      attachedTo: instance.instanceName,
    });

    staticIp.addDependency(instance);

    // ── 8. Stack outputs ──────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'PublicIp', {
      value: staticIp.attrIpAddress,
      description: 'Static public IP — point your DNS A record here',
    });

    new cdk.CfnOutput(this, 'EcrRepoUri', {
      value: ecrRepo.repositoryUri,
      description: 'ECR repository URI — used by scripts/push-image.sh',
    });

    new cdk.CfnOutput(this, 'SecretArn', {
      value: configSecret.secretArn,
      description: 'Secrets Manager ARN — update with your real API keys',
    });

    new cdk.CfnOutput(this, 'SSHCommand', {
      value: cdk.Fn.join('', ['ssh ubuntu@', staticIp.attrIpAddress]),
      description: 'SSH into the instance',
    });

    new cdk.CfnOutput(this, 'SetupLogCommand', {
      value: cdk.Fn.join('', [
        'ssh ubuntu@',
        staticIp.attrIpAddress,
        ' "sudo tail -f /var/log/nanobot-setup.log"',
      ]),
      description: 'Watch the bootstrap log in real time',
    });
  }
}
