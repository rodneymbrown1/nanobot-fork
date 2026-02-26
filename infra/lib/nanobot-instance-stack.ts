import * as cdk from 'aws-cdk-lib';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lightsail from 'aws-cdk-lib/aws-lightsail';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as fs from 'fs';
import * as path from 'path';
import { Construct } from 'constructs';

export interface NanobotInstanceStackProps extends cdk.StackProps {
  /** Unique name for this instance (e.g. "nano-alpha", "nano-beta"). */
  instanceName: string;
  /** ECR repository from shared stack. */
  ecrRepo: ecr.Repository;
  /** IAM access key ref from shared stack. */
  accessKeyRef: string;
  /** IAM access key secret from shared stack. */
  accessKeySecret: string;
  /** Org secret ARN from shared stack. */
  orgSecretArn: string;
  /** S3 bucket name for agent identity (optional). */
  agentBucket?: string;
}

/**
 * Per-instance resources:
 * - Lightsail instance
 * - Persistent data disk
 * - Static IP
 * - Instance-level secret (channels, gateway key)
 */
export class NanobotInstanceStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: NanobotInstanceStackProps) {
    super(scope, id, props);

    const { instanceName, ecrRepo, accessKeyRef, accessKeySecret, orgSecretArn } = props;
    const agentBucket = props.agentBucket ?? this.node.tryGetContext('agentBucket') ?? '';

    // Context-driven sizing
    const az: string = this.node.tryGetContext('availabilityZone') ?? `${this.region}a`;
    const bundleId: string = this.node.tryGetContext('bundleId') ?? 'small_3_0';
    const diskSizeGb: number = this.node.tryGetContext('diskSizeGb') ?? 20;

    // SSH CIDR — REQUIRED
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

    // ── 1. Instance secret ──────────────────────────────────────────────
    const instanceSecret = new secretsmanager.Secret(this, 'InstanceSecret', {
      secretName: `nanobot/instance/${instanceName}`,
      description: `Per-instance config for ${instanceName} — channels, gateway key, agent overrides.`,
      secretStringValue: cdk.SecretValue.unsafePlainText(
        JSON.stringify(
          {
            channels: {
              telegram: {
                enabled: false,
                token: 'REPLACE_ME',
                allowFrom: [],
              },
            },
            gateway: {
              apiKey: 'REPLACE_ME',
            },
          },
          null,
          2,
        ),
      ),
    });

    // ── 2. Persistent data disk ─────────────────────────────────────────
    const disk = new lightsail.CfnDisk(this, 'DataDisk', {
      diskName: `${instanceName}-data`,
      sizeInGb: diskSizeGb,
      availabilityZone: az,
    });

    // ── 3. User data (bootstrap script) ─────────────────────────────────
    const userDataTemplate = fs.readFileSync(
      path.join(__dirname, 'scripts', 'user-data.sh'),
      'utf-8',
    );

    const userDataScript = userDataTemplate
      .replace(/\$\{AWSAccessKeyId\}/g, accessKeyRef)
      .replace(/\$\{AWSSecretKey\}/g, accessKeySecret)
      .replace(/\$\{OrgSecretArn\}/g, orgSecretArn)
      .replace(/\$\{InstanceSecretArn\}/g, instanceSecret.secretArn)
      .replace(/\$\{AWS::AccountId\}/g, cdk.Aws.ACCOUNT_ID)
      .replace(/\$\{AWS::Region\}/g, cdk.Aws.REGION)
      .replace(/\$\{AgentBucket\}/g, agentBucket)
      .replace(/\$\{AgentInstance\}/g, instanceName);

    // ── 4. Lightsail instance ───────────────────────────────────────────
    const instance = new lightsail.CfnInstance(this, 'Instance', {
      instanceName,
      availabilityZone: az,
      blueprintId: 'ubuntu_22_04',
      bundleId,
      userData: userDataScript,
      networking: {
        ports: [
          { fromPort: 22, toPort: 22, protocol: 'tcp', cidrs: sshCidrs },
          { fromPort: 80, toPort: 80, protocol: 'tcp', cidrs: ['0.0.0.0/0'] },
          { fromPort: 443, toPort: 443, protocol: 'tcp', cidrs: ['0.0.0.0/0'] },
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
          addOnType: 'AutoSnapshot',
          autoSnapshotAddOnRequest: {
            snapshotTimeOfDay: '06:00',
          },
        },
      ],
    });

    instance.addDependency(disk);

    // ── 5. Static IP ────────────────────────────────────────────────────
    const staticIp = new lightsail.CfnStaticIp(this, 'StaticIp', {
      staticIpName: `${instanceName}-ip`,
      attachedTo: instance.instanceName,
    });

    staticIp.addDependency(instance);

    // ── Outputs ─────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'PublicIp', {
      value: staticIp.attrIpAddress,
      description: `Static public IP for ${instanceName}`,
    });

    new cdk.CfnOutput(this, 'InstanceSecretArn', {
      value: instanceSecret.secretArn,
      description: `Instance secret ARN for ${instanceName}`,
    });

    new cdk.CfnOutput(this, 'SSHCommand', {
      value: cdk.Fn.join('', ['ssh ubuntu@', staticIp.attrIpAddress]),
      description: `SSH into ${instanceName}`,
    });

    new cdk.CfnOutput(this, 'SetupLogCommand', {
      value: cdk.Fn.join('', [
        'ssh ubuntu@',
        staticIp.attrIpAddress,
        ' "sudo tail -f /var/log/nanobot-setup.log"',
      ]),
      description: `Watch bootstrap log for ${instanceName}`,
    });
  }
}
